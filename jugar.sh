#!/bin/bash
# Raft Recomp: lanza Raft nativo para Linux (OpenGL). El log queda en ultimo.log, al lado de este script.
cd "$(dirname "$(readlink -f "$0")")/game" || exit 1
exec ./Raft.x86_64 -logFile ../ultimo.log "$@"
