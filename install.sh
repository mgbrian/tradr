#!/bin/bash

SCRIPT_PATH=$(readlink -f "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(dirname "$SCRIPT_PATH")
VENV_DIR=$SCRIPT_DIR/.requirements

python3 -m venv $VENV_DIR || { echo "Error creating virtualenv."; exit 1; }

source $VENV_DIR/bin/activate || { echo "Error activating virtualenv."; exit 1; }

pip install -r requirements.txt
