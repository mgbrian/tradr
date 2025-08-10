#!/bin/bash

SCRIPT_PATH=$(readlink -f "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(dirname "$SCRIPT_PATH")
VENV_DIR=$SCRIPT_DIR/.requirements

if [ ! -d "$VENV_DIR" ]; then
    echo "Virtual environment not found. Running the installer..."

    ./install.sh || { echo "Error installing dependencies."; exit 1; }
fi

source $VENV_DIR/bin/activate || { echo "Error activating virtualenv."; exit 1; }

python main.py
