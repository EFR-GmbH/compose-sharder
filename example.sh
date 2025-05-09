#!/bin/sh

uv run compose-sharder.py \
	-i example/compose.yaml \
	-e example/compose.ext.yaml \
	-c example/config.env \
	-o example/tmp
