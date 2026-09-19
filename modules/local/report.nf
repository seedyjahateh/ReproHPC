process REPORT {
    cpus 1
    memory '1 GB'
    time '5 min'
    publishDir "${params.outdir}/report", mode: 'copy', failOnError: true
    input:
    path summary, stageAs: 'summary'
    path batches, stageAs: 'batch????'
    output:
    path 'index.html'
    script:
    def science_b64 = groovy.json.JsonOutput.toJson(params.science).bytes.encodeBase64().toString()
    """
    python -m reprohpc.task report --summary summary --params-b64 '${science_b64}' --output index.html batch????
    """
}

