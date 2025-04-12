#!/bin/bash

# Create a virtual enviroment for the project
python3 -m venv build

# Activate the virtual enviroment
chomod +x ./build/bin/activate
./build/bin/activate

# Use default settings
read -p "For default settings type 1, for custom 2, anything else to exit." choice

case $choice in
  1)
    ./default.sh
  2)
    ./custom.sh
  *)
    exit 0
esac







# Install requirements
./build/bin/pip3 install requirements.txt


