#!/bin/bash
set -e

if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Run setup first:"
    echo "  python3.11 -m venv venv"
    echo "  source venv/bin/activate"
    echo "  pip install -r requirements.txt"
    echo "  playwright install chromium"
    exit 1
fi

source venv/bin/activate
python main.py