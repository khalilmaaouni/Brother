#!/bin/bash
# usage (repo root): grade_lanes_par.sh <wave dir> [parallel=3] ; one line per lane: PASS <variant> or NO-PASS. First pass wins, later variants are not graded.
W=$1; P=${2:-3}
ls "$W"/out/*-r*-build.json 2>/dev/null | sed -E 's#.*/(.*)-r[0-9]+-build\.json#\1#' | sort -u | xargs -P "$P" -n 1 ~/.claude/bin/grade_lane.sh "$W"
