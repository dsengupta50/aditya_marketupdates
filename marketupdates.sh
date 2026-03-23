#!/bin/zsh

cd ~/Projects/aditya_marketupdates
source venv/bin/activate
venv/bin/pip install -r requirements.txt
venv/bin/pip list | grep -i flas
venv/bin/python app.py
