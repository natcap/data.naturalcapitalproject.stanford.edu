#!/bin/bash

# How To Run:
# 1. Make sure you have all relevant files in your textfile.
# Paths can be placed in the textfile via "list-datasets.py"
# 2. To use, run: sh update-datasets-from-list.sh --textfilename
# textfile name should be: all-paths.txt, global-paths.txt, 
# or regional-paths.txt

# This script will iterate over the paths in 
# the designated textfile and run create-or-update-dataset.py

# set your oak mount drive prefix
oak_prefix="Z:"

# Set the name of the textfile you want to iterate over
textfile=all-paths.txt

# Iterate over files in textfile
IFS=$'\r\n'       
set -f          # disable globbing
for i in $(cat $textfile); do
  echo "Updating: $oak_prefix/$i"
  python ../../create-or-update-dataset.py "$oak_prefix\\$i"
done