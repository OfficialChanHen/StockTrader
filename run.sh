#!/bin/bash
echo "Installing dependencies..."
pip install -r requirements.txt --quiet
echo "Done. Starting strategy..."
python trade_strategy.py