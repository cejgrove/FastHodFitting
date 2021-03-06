#!/bin/bash -l

#SBATCH --ntasks 1
#SBATCH -J satsat_onehalo
#SBATCH -o ./logs/%x.%J.out
#SBATCH -p cosma7
#SBATCH -A dp004
#SBATCH --exclusive
#SBATCH -t 4:00:00
#SBATCH --mail-type=END    # notifications for job
#SBATCH --mail-user=cameron.grove@durham.ac.uk


module purge
module load python/3.6.5

python3 satsat_onehalo.py



