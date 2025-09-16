""" This script will serve to search through the catalog folders on Oak and
    create a list of YAML paths

    Dependencies:
        mamba install geometamaker os glob sys

    run this command at your shell or from bash script:
        python list-datasets.py "path to files" > textfile name

    NOTE: the output file may require some manual editing
    to remove paths of files not on the Hub yet

"""
import os
import glob
import sys


def extract_paths(hub_dir):

    """Create txt file listing all *.yml paths for
        Data Hub Datasets. Will be looped over by 
        create-or-update-dataset.py.

    Args:
        hub_dir (path): the path to the Oak directory
        with Data Hub data.

    Returns:
        Prints a list of all catalog yaml files to standard out.
    """

    hub_dir = os.path.abspath(hub_dir)
    tiffiles = []
    for tif in glob.glob(os.path.join(hub_dir, "*/*.yml"), recursive=True):
        if tif.endswith("_cog.tif.yml"):
            pass  # these are unfinished or test files
        elif tif.endswith("initial.tif.yml"):
            pass  # these are archived files
        else:
            tif = os.path.abspath(tif)
            # remove Drive prefix for relative path
            tif_path_noprefix = tif.split("\\", 1)[1]
            tiffiles.append(tif_path_noprefix)
    print(*tiffiles, sep='\n')


if __name__ == '__main__':
    extract_paths(sys.argv[1])
