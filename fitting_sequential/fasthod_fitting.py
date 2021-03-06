# fasthod_fitting.py
"""
A module for all the functions used in my code to fit HOD parameters
This should be imported at the start of any scripts
"""

import numpy as np
import matplotlib
matplotlib.use('agg')
import matplotlib.pyplot as plt
import h5py
import sys
import time
import emcee
from multiprocessing import Pool
import config
import config_fitting
# Load parameters from config file
path = config.path
boxsize = config.boxsize
r_bin_edges = config.r_bin_edges
mass_bin_edges = config.mass_bin_edges
num_sat_parts = config.num_sat_parts
run_label = config.run_label
subsample_array = config.subsample_array
# Load parameters from fitting config file

num_steps = config_fitting.num_steps
num_walkers = config_fitting.num_walkers
run_path = config_fitting.run_path
Cen_HOD = config_fitting.Cen_HOD
spline_kernel_integral = config_fitting.spline_kernel_integral
cumulative_spline_kernel = config_fitting.cumulative_spline_kernel
Sat_HOD = config_fitting.Sat_HOD
likelihood_calc = config_fitting.likelihood_calc
target_2pcf = config_fitting.target_2pcf
target_num_den = config_fitting.target_num_den
err = config_fitting.err
num_den_err = config_fitting.num_den_err
priors = config_fitting.priors
initial_params_random = config_fitting.initial_params_random
initial_params = config_fitting.initial_params
num_mass_bins_big = config_fitting.num_mass_bins_big
random_seed = config_fitting.random_seed

save_path = config_fitting.save_path


# Parameters to include in the fit
included_params  = config_fitting.included_params
mm_flag = config_fitting.mm_flag
si_flag = config_fitting.si_flag
m0_flag = config_fitting.m0_flag
m1_flag = config_fitting.m1_flag
al_flag = config_fitting.al_flag

mm_index = config_fitting.mm_index
si_index = config_fitting.si_index
m0_index = config_fitting.m0_index
m1_index = config_fitting.m1_index
al_index = config_fitting.al_index




def initialise_walkers(initial_params_random,initial_params,priors,num_walkers):
    """
    Initialise the positions of the walkers for fitting the HOD parameters
    Do this randomly within the prior space if initial_params_random=True
    Else populate in a small region around some provided params
    """
    pos = np.zeros((num_walkers,np.shape(priors)[0]))
    if (initial_params_random):
        for i in range(num_walkers):
            for j in range(np.shape(priors)[0]):
                pos[i,j] = np.random.uniform(priors[j,0],priors[j,1])

    else:
        if len(initial_params)!=np.shape(priors)[0]:
            raise ValueError("Your initial parameter values and priors have different shapes")
        for i in range(num_walkers):
            # Populate in a 10% region around parameters provided
            # Potential to make the size of this region an input variable if necessary
            pos[i,:] = initial_params*(0.95 + 0.1*np.random.random(np.shape(priors)[0]))
    # print(pos)

    # Check none of the walkers lie outside the prior space
    for i in range(num_walkers):
        for j in range(np.shape(priors)[0]):
            if pos[i,j] < priors[j,0]:
                raise ValueError("Your initial parameter values lie outside the prior space, parameter ",j, " is too low")
            if pos[i,j] > priors[j,1]:
                raise ValueError("Your initial parameter values lie outside the prior space, parameter ",j, " is too high")
    return pos

        
def undo_subsampling(mass_pair_array,subsample_array):
    """
    Change a halo paircount table to undo the effect of subsampling
    """
    altered_mass_pair_array = np.zeros(np.shape(mass_pair_array))
    for i in range(len(mass_pair_array[:,0,0])):
        for j in range(len(mass_pair_array[:,0,0])):
            altered_mass_pair_array[i,j,:] = mass_pair_array[i,j,:] / (subsample_array[i]*subsample_array[j])
    return altered_mass_pair_array 

def calc_hmf(path, num_mass_bins_big,mass_bin_edges):
    """
    Calculate the hmf from the catalog
    """
    snap = h5py.File(path,"r")
    Mvir = snap["/mass"][:]*1e10
    is_central = snap["/is_central"][:]


    mass_centrals = Mvir[is_central]
    mass_sats = Mvir[~np.array(is_central)]

    mass_min = mass_bin_edges[0]
    mass_max = mass_bin_edges[-1]
    
    
    # Go for a large number of sub bins for accuracy
    mass_bins_big = np.logspace(np.log10(mass_min),np.log10(mass_max),num_mass_bins_big + 1)
    cen_halos_big = np.histogram(mass_centrals,bins = mass_bins_big)[0]
    sat_halos_big = np.histogram(mass_sats,bins = mass_bins_big)[0]
    
    return mass_bins_big, cen_halos_big, sat_halos_big

def calc_hmf_ascii(path, num_mass_bins_big,mass_bin_edges):
    """
    Calculate the hmf from the catalog
    """

    path = sys.argv[1]
    Mvir, PID = np.loadtxt(path,usecols=(2,41),unpack=True)


    # Only take the central halos as we shall create our own satellite particles later
    mask1 = np.where(PID == -1)

    Mvir = Mvir[mask1]


    mass_min = mass_bin_edges[0]
    mass_max = mass_bin_edges[-1]


    # Go for a large number of sub bins for accuracy
    mass_bins_big = np.logspace(np.log10(mass_min),np.log10(mass_max),num_mass_bins_big + 1)
    cen_halos_big = np.histogram(Mvir,bins = mass_bins_big)[0]
    sat_halos_big = np.histogram(Mvir,bins = mass_bins_big)[0]

    return mass_bins_big, cen_halos_big, sat_halos_big
 


  
def create_weighting_factor(mass_pair_array,hod1,hod2):
    """
    Multiply the array by the relevant HODs and then sum over the mass bins
    to get the number of pairs as a function of r. These can then be divided
    by the randoms to get the correlation function.
    """
    weighting_factor = np.tensordot(np.outer(hod1,hod2),mass_pair_array,axes=([0,1],[0,1]))
    return weighting_factor

def create_accurate_HOD(hod,halos,mass_bin_edges,num_mass_bins_big):
    """
    Using just 100-400 mass bins for the HOD isn't accurate enough. Take much smaller
    mass subdivisions and use these to create an accurate HOD for only 100-400 mass bins.
    """
    num_mass_bins_small = int(len(mass_bin_edges) -1)
    mass_bins_factor = int(num_mass_bins_big/num_mass_bins_small)
    if num_mass_bins_big % num_mass_bins_small != 0:
        raise ValueError("finer grained mass bins do not evenly divide coarser mass bins:",num_mass_bins_small," is not a factor of ",num_mass_bins_big)
    hod[np.isnan(hod)] = 0
    HOD_halo_product = halos * hod
    HOD_recalc = np.sum(np.reshape(HOD_halo_product,(num_mass_bins_small,mass_bins_factor)),axis=1) / (
                 np.sum(np.reshape(halos,(num_mass_bins_small,mass_bins_factor)),axis=1))
    HOD_recalc[np.isnan(HOD_recalc)] = 0
    return HOD_recalc


def create_2pcf(hod_cen,hod_sat,cencen,censat,satsat,satsat_onehalo,num_sat_parts,
                hod_cen_big,hod_sat_big,cen_halos_big,sat_halos_big,
                boxsize):
    """
    Creates the 2pcf from the hod and the paircounts.
    First create the total number of paircounts.
    Then create the analytic randoms based on the total galaxies expected
    Then create the correlation function from these two components
    """
    
    weighting_factor_cencen = create_weighting_factor(cencen,hod_cen,hod_cen)
    # Multiply censat factor by two as the others are double counted but this one isn't
    weighting_factor_censat = create_weighting_factor(censat,hod_cen,hod_sat)*2
    weighting_factor_satsat = create_weighting_factor(satsat,hod_sat,hod_sat)
    weighting_factor_satsat_onehalo = create_weighting_factor(satsat_onehalo,hod_sat,hod_sat) / (
                                      (num_sat_parts*(num_sat_parts-1))/2)

    weighting_factor_total = weighting_factor_cencen + weighting_factor_censat + (
                             weighting_factor_satsat + weighting_factor_satsat_onehalo)

    npart = np.sum(cen_halos_big * hod_cen_big)
    npart_sat = np.sum(sat_halos_big * hod_sat_big / num_sat_parts)
    randoms = create_randoms_for_corrfunc(boxsize = boxsize,
                                          npart = (npart + npart_sat),
                                          r_bin_edges = r_bin_edges)
    cf = (weighting_factor_total / randoms) - 1
    return cf



def create_randoms_for_corrfunc(npart,r_bin_edges,boxsize):
    """
    Calculate the analytic randoms for npart particles in a box with
    side length boxsize.  This code is based on the calculation done 
    either in halotools or corrfunc but the formula is pretty simple.
    """
    NR = npart

    # do volume calculations
    v = (4./3.)*np.pi*r_bin_edges**3 # volume of spheres
    dv = np.diff(v)  # volume of shells
    global_volume = boxsize**3  # volume of simulation

    # calculate the random-random pairs using density * shell volume
    rhor = (NR*(NR-1))/global_volume
    RR = (dv*rhor)
    return RR

def calc_number_density(hod_cen_big,hod_sat_big,
                        cen_halos_big,boxsize):
    """
    A function which caluculates the number density of halos from the hod
    and a mass function
    """
    num_density = (np.sum(hod_cen_big*cen_halos_big) + np.sum(hod_sat_big*cen_halos_big)) / (boxsize**3)

    return num_density
    
def log_likelihood(params, target, target_density):
    """
    A function to convert from the parameters to the log-likelihood
    Involves creating the 2pcf, then comparing it to the model
    Also includes matching the number density as well
    """
    hod_cen_big = Cen_HOD(params,mass_bin_centres_big,Mmin,sigma_logM)
    hod_sat_big = Sat_HOD(params,hod_cen_big,mass_bin_centres_big,M0,M1,alpha)
    
    
    hod_cen = create_accurate_HOD(hod_cen_big,cen_halos_big,mass_bin_edges,num_mass_bins_big)
    hod_sat = create_accurate_HOD(hod_sat_big,sat_halos_big,mass_bin_edges,num_mass_bins_big)   
    cf = create_2pcf(hod_cen,hod_sat,cencen,censat,satsat,satsat_onehalo,num_sat_parts,
                hod_cen_big,hod_sat_big,cen_halos_big,sat_halos_big,
                boxsize)

    number_density = calc_number_density(hod_cen_big,hod_sat_big,
                        cen_halos_big,boxsize)

    # Likelihood from fitting cf
    likelihood = likelihood_calc(cf,target,err) 
    
    # Likelihood from number density fit
    likelihood_num_den =  - (1 - (number_density / target_density))**2 / (num_den_err**2)
    
    # Add them both together to get the total likelihood
    l_likelihood = likelihood + likelihood_num_den
    return l_likelihood
    
    
def log_prior(theta):
    """
    Returns whether the parameters are within the priors or not
    If parameters are outside any priors then return a -inf log probability
    """
    outside_prior_counter = 0
    for i in range(np.shape(priors)[0]):
        if ((theta[i] < priors[i,0]) or (theta[i] > priors[i,1]) ):
            outside_prior_counter +=1
    if outside_prior_counter == 0:
        return 0.0
    else:
        return -np.inf


def log_probability(theta, y, yerr):
    """
    Converts the parameter values, target 2pcf, and error into a probability for emcee to use
    """
    lp = log_prior(theta)
    if not np.isfinite(lp):
        return -np.inf
    ll = log_likelihood(theta, y, yerr)
    if not np.isfinite(ll):
        return -np.inf
    return lp + ll


def perform_fitting(target_number):
    """
    A function to perform the fitting of the parameters
    Takes only target number as an input, this is the index
    of the magnitude limit which is being fitted. This number
    will be iterated over
    """
    walker_init_pos = initialise_walkers(initial_params_random,initial_params,priors,num_walkers)
    
    target = target_2pcf[:,target_number+1]
    target_density = 10**target_num_den[target_number,1]
    print("Luminosity limit: ",target_num_den[target_number,0])
    print("Target Density: ",10**target_num_den[target_number,1])
    nwalkers, ndim = walker_init_pos.shape
    start_time = time.time()
    #with Pool() as pool:
    sampler = emcee.EnsembleSampler(nwalkers, ndim, log_probability, args=(target, target_density))
    sampler.run_mcmc(walker_init_pos, num_steps);
    end_time = time.time()
    print("fitting took ", end_time - start_time, " seconds")
    return sampler

def plot_HODs(HODs):
    """
    Plot HOD/s resulting from fits
    """
    plt.figure(figsize = (8,8))
    for i in range(len(HODs[0,:])):
        plt.plot(mass_bin_edges[:-1] + np.diff(mass_bin_edges)/2,HODs[:,i],c="C"+str(i),label=i)
    plt.ylim(1e-3)
    plt.yscale("log")
    plt.xscale("log")
    plt.legend(title = "Magnitude")
    plt.ylabel("n")
    plt.xlabel("Halo Mass /Solar Masses")
    plt.savefig(save_path+"_HODs.png",bbox_inches="tight")
    plt.close()
    return 0

def plot_CFs(CFs):
    """
    Plot CF ratio/s from fits
    """
    #plt.figure(figsize = (10,8))
    for i in range(len(CFs[0,:])):
        r_bin_centres = 10**(np.log10(r_bin_edges[:-1]) + np.diff(np.log10(r_bin_edges))/2 )
        plt.plot(r_bin_centres,CFs[:,i],c="C"+str(i))#,linestyle="--",alpha=0.6)
    plt.xscale("log")
    plt.xlabel("r [Mpc/h]")
    plt.ylabel(r"$\xi$(r) ratio")
    plt.legend(title = "Magnitude")
    plt.ylim(0.5,2)
    plt.grid()
    plt.savefig(save_path+"_corrfunc_ratio.png",bbox_inches="tight")
    plt.close()
    return 0


def max_like_params(sampler):
    """Get the parameters which provide the maximum likelihood from the sampling"""

    # Only take after the first 1000 steps to allow burn in
    flat_samples = sampler.backend.get_chain()[:,:,:]
    likelihoods = sampler.backend.get_log_prob()[:,:]

    # Find the maximum likelihood parameter position

    print(np.shape(likelihoods))
    best_param_index1 = np.argmax(likelihoods,axis = 0)
    best_param_index2 = np.argmax(samplers[0].get_log_prob()[best_param_index1,np.arange(20)])
    best_params = flat_samples[best_param_index1[best_param_index2],best_param_index2,:]
    print(best_param_index1[best_param_index2],best_param_index2)
    print(samplers[0].get_log_prob()[best_param_index1[best_param_index2],best_param_index2])
    return best_params



# Run this directly to perform the fitting:

if __name__ == "__main__":

    # First get the number of halos and the hmf and the fine grained mass bins

    mass_bins_big, cen_halos_big, sat_halos_big = calc_hmf(path, num_mass_bins_big, mass_bin_edges)

    np.save("mass_bins_big.npy",mass_bins_big)
    np.save("cen_halos_big.npy",cen_halos_big)
    np.save("sat_halos_big.npy",sat_halos_big)
    
    # Alternatively if you've already got the binned halo masses you can just load the files

    # mass_bins_big = np.load("mass_bins_big.npy")
    # cen_halos_big = np.load("cen_halos_big.npy")
    # sat_halos_big = np.load("sat_halos_big.npy")

    # Now get the finer grained mass bin centres for accuarate HOD estimation

    mass_bin_centres_big = mass_bins_big[:-1] + np.diff(mass_bins_big)/2
    
    # Now load in the mass - r binned paircounts:
    print(run_path)
    cencen = np.load(run_path + "_cencen.npy")
    censat = np.load(run_path + "_censat.npy")
    satsat = np.load(run_path + "_satsat.npy")
    satsat_onehalo = np.load(run_path + "_satsat_onehalo.npy")
    
    # Undo the subsampling

    cencen = undo_subsampling(cencen,subsample_array)
    censat = undo_subsampling(censat,subsample_array)
    satsat = undo_subsampling(satsat,subsample_array)
    # Set the random seed

    np.random.seed(random_seed)

    # Do the fitting for each magnitude limit:
    samplers = []

    
    # Read in files for fixed parameters
    # Read these in in all cases (they just won't be used if the parameter is not fixed yet)
    Mmins = np.genfromtxt("Mmin_fit.txt")
    sigma_logMs = np.genfromtxt("sigma_logM_fit.txt")
    M0s = np.genfromtxt("M0_fit.txt")
    M1s = np.genfromtxt("M1_fit.txt")
    alphas = np.genfromtxt("alpha_fit.txt")
    for i in range(9):
        Mmin = Mmins[i]
        sigma_logM = sigma_logMs[i]
        M0 = M0s[i]
        M1 = M1s[i]
        alpha = alphas[i]

        
        samples = perform_fitting(i)
        samplers.append(samples)
        
        # Print the parameters at the end of the chain to check they are reasonable
        print("Parameters at the end of the fitting chain: (These aren't best fits, just a sanity check)")
        print(samples.backend.get_chain()[-1,0,:])

    print("All done!")
    print("Saving outputs")

    # Take the parameters and probabilities and save these as numpy arrays
    # Alternative is pickling the samplers object but can be hard to unpickle
    # if the modules used aren't identical
    
    samplers_to_save_params = np.zeros((num_steps,num_walkers,np.shape(priors)[0],9))
    samplers_to_save_probs = np.zeros((num_steps,num_walkers,9))
    for i in range(9):
        samplers_to_save_params[:,:,:,i] = samplers[i].backend.get_chain()
        samplers_to_save_probs[:,:,i] = samplers[i].backend.get_log_prob()
    
    np.save(save_path+"_params.npy",samplers_to_save_params)
    np.save(save_path+"_log_probs.npy",samplers_to_save_probs)


    # Plot the results
    # Find the max likelihood parameters

    best_params = np.zeros((9,len(initial_params)))
    for i in range(9):
        best_params[i,:] = max_like_params(samplers[i])
        print(best_params[i,:])
    
    # Here save the best fit parameters using either the fitted or pre-fixed values
    best_fit_params = np.zeros((9,6))
    best_fit_params[:,0] =  target_num_den[:,0]
    # For consistency with alex's code first column is magnitudes
    if mm_flag == 1:
        best_fit_params[:,1] = best_params[:,mm_index]
    else:
        best_fit_params[:,1] = np.genfromtxt("Mmin_fit.txt")
    
    if si_flag == 1:
        best_fit_params[:,2] = best_params[:,si_index]
    else:
        best_fit_params[:,2] = np.genfromtxt("sigma_logM_fit.txt")

    if m0_flag == 1:
        best_fit_params[:,3] = best_params[:,m0_index]
    else:
        best_fit_params[:,3] = np.genfromtxt("M0_fit.txt")

    if m1_flag == 1:
        best_fit_params[:,4] = best_params[:,m1_index]
    else:
        best_fit_params[:,4] = np.genfromtxt("M1_fit.txt")

    if al_flag == 1:
        best_fit_params[:,5] = best_params[:,al_index]
    else:
        best_fit_params[:,5] = np.genfromtxt("alpha_fit.txt")


    np.savetxt("hod_params/params/fit_params.txt",best_fit_params)
    
    # Plot the HODs and Correlation Function Ratios - 
    # plotting parameters is hard without knowledge of how many there are
    
    HODs = np.zeros((len(mass_bin_edges)-1,9))
    CF_ratio = np.zeros((len(r_bin_edges)-1,9))
    CFs = np.zeros((len(r_bin_edges)-1,9))    
    Num_dens = np.zeros(9)
    for i in range(9):
        params = best_params[i,:]
        Mmin = Mmins[i]
        sigma_logM = sigma_logMs[i]
        M0 = M0s[i]
        M1 = M1s[i]
        alpha = alphas[i]

        hod_cen_big = Cen_HOD(params,mass_bin_centres_big,Mmin,sigma_logM)
        hod_sat_big = Sat_HOD(params,hod_cen_big,mass_bin_centres_big,M0,M1,alpha)


        hod_cen = create_accurate_HOD(hod_cen_big,cen_halos_big,mass_bin_edges,num_mass_bins_big)
        hod_sat = create_accurate_HOD(hod_sat_big,sat_halos_big,mass_bin_edges,num_mass_bins_big)
        cf = create_2pcf(hod_cen,hod_sat,cencen,censat,satsat,satsat_onehalo,num_sat_parts,
                hod_cen_big,hod_sat_big,cen_halos_big,sat_halos_big,
                boxsize)
        HODs[:,i] = hod_cen + hod_sat
        CF_ratio[:,i] = cf / target_2pcf[:,i+1]
        CFs[:,i] = cf
        #print(CF_ratio)
        num_den = calc_number_density(hod_cen_big,hod_sat_big,
                        cen_halos_big,boxsize)
        Num_dens[i] = num_den
    r_bin_centres = 10**(np.log10(r_bin_edges[:-1]) + np.diff(np.log10(r_bin_edges))/2 )
    cf_to_save = np.zeros((len(r_bin_centres),10))
    cf_to_save[:,0] = r_bin_centres
    cf_to_save[:,1:] = CFs
    np.savetxt(save_path+"_CF.txt",cf_to_save)
    np.savetxt(save_path+"_num_den.txt",Num_dens)
    plot_CFs(CF_ratio)
    plot_HODs(HODs)
