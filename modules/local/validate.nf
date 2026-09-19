process VALIDATE_DATASET {
    cache false
    cpus 1
    memory '1 GB'
    time '30 min'
    input:
    path data_dir, stageAs: 'dataset'
    path ref_dir, stageAs: 'reference'
    path manifest, stageAs: 'samples.csv'
    val dataset_name
    val reference_name
    output:
    path 'validated.json'
    script:
    // Metadata basenames are restricted by CLI preflight.
    """
    python -m reprohpc.task validate --dataset dataset/${dataset_name} --manifest samples.csv --reference reference/${reference_name} --output validated.json
    """
}

