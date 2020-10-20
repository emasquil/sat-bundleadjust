import numpy as np
from bundle_adjust import ba_core

def build_connectivity_matrix(C, min_matches=10):

    '''
    the connectivity matrix A is a matrix with size NxN, where N is the numbe of cameras
    the value at posiition (i,j) is equal to the amount of matches found between image i and image j
    '''
    
    n_cam = int(C.shape[0]/2)
    A, n_correspondences_filt = np.zeros((n_cam,n_cam)), []
    for im1 in range(n_cam):
        for im2 in range(im1+1,n_cam):
            n_matches = np.sum(1*np.logical_and(1*~np.isnan(C[2*im1, :]), 1*~np.isnan(C[2*im2, :])))
            n_correspondences_filt.append(n_matches)
            A[im1, im2] = n_matches if n_matches >= min_matches else 0
            A[im2, im1] = n_matches if n_matches >= min_matches else 0
            
    return A


def reprojection_error_from_C(C, pts3d, cameras, cam_model, pairs_to_triangulate):

    # set ba parameters
    from bundle_adjust.ba_params import BundleAdjustmentParameters
    p = BundleAdjustmentParameters(C, pts3d, cameras, cam_model, pairs_to_triangulate,
                                   n_cam_fix=0, n_pts_fix=0, reduce=False, verbose=False)

    # compute reprojection error at the initial parameters
    reprojection_err_per_obs = ba_core.compute_reprojection_error(ba_core.fun(p.params_opt.copy(), p))
    
    # create the equivalent of C but fill the slot of each observation with the corresponding reprojection error
    n_cam, n_pts = int(C.shape[0] / 2), int(C.shape[1])
    C_reproj = np.zeros((n_cam, n_pts))
    C_reproj[:] = np.nan
    for i, err in enumerate(reprojection_err_per_obs):
        C_reproj[p.cam_ind[i], p.pts_ind[i]] = err
    
    return C_reproj


def compute_camera_weights(C, C_reproj, connectivity_matrix=None):
    
    n_cam = int(C.shape[0]/2)
    
    if connectivity_matrix is None:
        A = build_connectivity_matrix(C)
    else:
        A = connectivity_matrix
    
    w_cam = []
    for i in range(n_cam):
    
        nC_i = np.sum(A[i, :]>0) 
        
        if nC_i > 0:
            indices_of_tracks_seen_in_current_cam = np.arange(C.shape[1])[~np.isnan(C[i*2,:])]
            
            # reprojection error of all tracks in the current cam
            #reproj_err_current_cam = C_reproj[i, indices_of_tracks_seen_in_current_cam]
            #avg_cost = np.mean(reproj_err_current_cam)
            #std_cost = np.std(reproj_err_current_cam)
            
            #mean and std of the average reprojection error of the tracks seen in the current camera
            avg_reproj_err_tracks_seen = np.nanmean(C_reproj[:, indices_of_tracks_seen_in_current_cam], axis=0)
            avg_cost = np.mean(avg_reproj_err_tracks_seen)
            std_cost = np.std(avg_reproj_err_tracks_seen)
            
            costC_i = avg_cost + 3. * std_cost
            
        else:
            costC_i = 0.
    
        w_cam.append( float(nC_i) + np.exp( - costC_i ) )
   
    
    return w_cam


def order_tracks(C, pts3d, cameras, cam_model, pairs_to_triangulate, priority=['length', 'cost']):
    
    C_reproj = reprojection_error_from_C(C, pts3d, cameras, cam_model, pairs_to_triangulate)

    tracks_cost = np.nanmean(C_reproj, axis=0)
    
    tracks_len = (np.sum(~np.isnan(C), axis=0)/2).astype(int)
    
    #tracks_scale = [] # to do
    #tracks_dtype = [('length', int), ('scale', float), ('cost', float)]
    #track_values = np.array(list(zip(tracks_len, -tracks_scale, -tracks_cost)), dtype=tracks_dtype)
    #ranked_track_indices = np.argsort(track_values, order=['length', 'scale', 'cost'])[::-1]
    
    tracks_dtype = [('length', int), ('cost', float)]
    track_values = np.array(list(zip(tracks_len, -tracks_cost)), dtype=tracks_dtype)
    ranked_track_indices = dict(list(zip(np.argsort(track_values, order=priority)[::-1], \
                                         np.arange(len(track_values)))))
    '''
    ranked_track_indices is a dict
    key = index of track in C
    value = position in track ranking
    '''
    
    return ranked_track_indices, C_reproj


def get_inverted_track_list(C, ranked_track_indices):
    
    inverted_track_list = {}
    n_cam = int(C.shape[0]/2)
    for i in range(n_cam):
        indices_of_tracks_seen_in_current_cam = np.arange(C.shape[1])[~np.isnan(C[i*2,:])]
        #print('cam:', i, ', tracks:', len(indices_of_tracks_seen_in_current_cam))
        inverted_track_list[i] = sorted(indices_of_tracks_seen_in_current_cam, key=lambda idx: ranked_track_indices[idx])
        
    return inverted_track_list


def select_best_tracks_adj_cams(n_new, C, pts3d, cameras, cam_model, pairs_to_triangulate,
                                K=30, debug=False, verbose=True):
    
    
    true_where_new_track = np.sum(~np.isnan(C[np.arange(0, C.shape[0], 2), :])[-n_new:]*1,axis=0).astype(bool)
    C_new = C[:, true_where_new_track]
    prev_track_indices = np.arange(len(true_where_new_track))[true_where_new_track]
    
    
    selected_track_indices = select_best_tracks(C_new, pts3d, cameras, cam_model,
                                                pairs_to_triangulate, K, debug, verbose=verbose)
    selected_track_indices = prev_track_indices[np.array(selected_track_indices )]
    
    return selected_track_indices.tolist()
    

def select_best_tracks(C, pts3d, cameras, cam_model, pairs_to_triangulate, K=30, debug=False, verbose=True):
    
    '''
    from 
    Tracks selection for robust, efficient and scalable large-scale structure from motion
    H Cui, Pattern Recognition (2017)
    '''
    
    import timeit
    start = timeit.default_timer()
    
    n_cam = int(C.shape[0]/2)
    V = np.arange(n_cam).tolist()  # all cam nodes
    
    ranked_track_indices, C_reproj = order_tracks(C, pts3d, cameras, cam_model, pairs_to_triangulate)
    remaining_T = np.arange(C.shape[1])
    T = np.arange(C.shape[1])
    
    k = 0
    S = []
    
    updated_C = C.copy()

    while k < K and len(S) < len(T):
    
        
        tracks_already_selected = list(set(T) - set(remaining_T))
        for idx in tracks_already_selected:
            updated_C[:,idx] = np.nan
        
        if debug and k > 0:
            if k > 0:
                tracks_cost = np.nanmean(C_reproj[:, tracks_already_selected], axis=0)
            else:
                tracks_cost = np.nanmean(C_reproj, axis=0)
            avg_reproj_err = np.mean(tracks_cost)
            print('k =', k, 'tracks already selected:', len(tracks_already_selected), 'avg reproj err:', avg_reproj_err)
        
           
        A = build_connectivity_matrix(updated_C)
        inverted_track_list = get_inverted_track_list(updated_C, ranked_track_indices)
        l = 1
        
        camera_weights = compute_camera_weights(updated_C, C_reproj, connectivity_matrix=A)
        Croot = np.argmax(camera_weights)

        Sk = []
        Ik = [Croot]
        nodes_last_layer_Hk = [Croot]
        
        iterate_current_tree = True
        while iterate_current_tree:
            nodes_next_layer_Hk = []
            for cam_idx in nodes_last_layer_Hk:
                for track_idx in inverted_track_list.get(cam_idx):
                    if track_idx not in Sk:
                        # visible_cams_track_idx
                        Wq = [k for k, j in enumerate(range(n_cam)) if not np.isnan(updated_C[j*2,track_idx])] 
                        # neighbor_cams_cam_idx
                        Rq = np.arange(n_cam)[A[cam_idx, :]>0] 
                        Zq = np.intersect1d(Wq, Rq).tolist() 
                        if len(Zq) > 0 and len(Zq) > len(np.intersect1d(Zq,Ik).tolist()): 
                            nodes_next_layer_Hk.extend(list(set(Zq) - set(np.intersect1d(Zq,Ik))))
                            Sk.extend([track_idx])
                            Ik.extend(Zq)
            l += 1
            h = len(nodes_last_layer_Hk)
            if len(list(set(Ik) - set(V))) == 0 or h == 0:
                iterate_current_tree = False
            nodes_last_layer_Hk = nodes_next_layer_Hk.copy()
        
        k += 1
        remaining_T = list(set(remaining_T) - set(Sk))
        S.extend(Sk)

    if verbose:
        print('Selected {} tracks out of {}\n'.format(len(S), len(T)))
        print('...done in {0:.2f} seconds\n'.format(timeit.default_timer()-start))
    
    return S


def get_utm_stats(C_utm):
    
    utm_dict = {}
    all_utm_distances = []
    
    n_img = int(C_utm.shape[0]/2)
    
    for p_ind in range(C_utm.shape[1]):
        im_ind = [k for k, j in enumerate(range(n_img)) if not np.isnan(C_utm[j*2,p_ind])]
    
        utm_distances = []
        for tmp_i in range(len(im_ind)):
            for tmp_j in np.arange(tmp_i+1,len(im_ind)):
                i, j = im_ind[tmp_i], im_ind[tmp_j]
                utm_i, utm_j = C_utm[(i*2):(i*2+2),p_ind], C_utm[(j*2):(j*2+2),p_ind]
                utm_distances.append(np.linalg.norm(utm_i - utm_j))
        
        all_utm_distances.extend(utm_distances)   
        utm_dict[p_ind] = utm_distances
        
    return utm_dict, all_utm_distances