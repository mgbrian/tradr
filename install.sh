#!/bin/bash

SCRIPT_PATH=$(readlink -f "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(dirname "$SCRIPT_PATH")
VENV_DIR=$SCRIPT_DIR/.requirements

python3 -m venv $VENV_DIR || { echo "Error creating virtualenv."; exit 1; }

source $VENV_DIR/bin/activate || { echo "Error activating virtualenv."; exit 1; }

pip install -r requirements.txt

if [ -f sample_env.py ] && [ ! -f env.py ]; then
    echo ""
    echo "Creating env.py from main repository's sample_env.py..."
    cp sample_env.py env.py || { echo "Failed to create env.py."; exit 1; }
fi

if [ -f env.py ]; then
    echo ""
    echo "==========="
    echo "   TODO:"
    echo "==========="
    echo "- Please update the variables in env.py accordingly."

    # Reminder to install and migrate Django database. The modified manage.py
    # here ensures the dabatase is created if it doesn't exist...
    if [ -f manage.py ]; then
        echo "- Once you've added the DB env variables, run python3 manage.py migrate..."
    fi
fi
