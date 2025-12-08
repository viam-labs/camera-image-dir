#!/bin/bash
cd `dirname $0`

if [ -f .installed ]
  then
    source venv/bin/activate
  else
    python3 -m pip install --user virtualenv --break-system-packages
    python3 -m venv venv
    source venv/bin/activate
    python3 -m pip install -e ".[dev]"
    if [ $? -eq 0 ]
      then
        touch .installed
    fi
fi

# Be sure to use `exec` so that termination signals reach the python process,
# or handle forwarding termination signals manually
exec "$(which python3)" -m src.main "$@"
