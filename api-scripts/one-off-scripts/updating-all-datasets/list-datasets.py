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
    yamlfiles = []
    for yml in glob.glob(os.path.join(hub_dir, "*/*.yml"), recursive=True):
        if yml.endswith("_cog.tif.yml"):
            pass  # these are unfinished or test files
        elif yml.endswith("initial.tif.yml"):
            pass  # these are archived files
        else:
            yml = os.path.abspath(yml)
            # remove Drive prefix for relative path
            yml_path_noprefix = yml.split("\\", 1)[1]
            yamlfiles.append(yml_path_noprefix)
    print(*yamlfiles, sep='\n')


if __name__ == '__main__':
    extract_paths(sys.argv[1])
