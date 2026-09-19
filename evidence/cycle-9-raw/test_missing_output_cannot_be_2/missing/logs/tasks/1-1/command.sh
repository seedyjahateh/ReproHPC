#!/bin/bash -ue
python -m reprohpc.task validate --dataset dataset/dataset.json --manifest samples.csv --reference reference/reference.json --output validated.json
