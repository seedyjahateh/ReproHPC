process AGGREGATE {
    cpus 1
    memory '2 GB'
    time '15 min'
    publishDir "${params.outdir}", mode: 'copy', failOnError: true
    input:
    path expected, stageAs: 'validated.json'
    path batches, stageAs: 'batch????'
    output:
    path 'summary'
    script:
    """
    python -m reprohpc.task aggregate --expected validated.json --output summary batch????
    """
}
