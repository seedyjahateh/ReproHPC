process ANALYZE_BATCH {
    tag "$batch_id"
    cpus 1
    memory { params.analysis_memory }
    time { params.analysis_time }
    maxRetries 1
    errorStrategy { task.exitStatus == 75 ? 'retry' : 'terminate' }
    publishDir "${params.outdir}", mode: 'copy', pattern: 'result/samples/*/*', saveAs: { name -> name.replaceFirst('^result/','') }, failOnError: true
    input:
    tuple val(batch_id), val(spec_b64), val(expected_files), path(images, stageAs: 'image????.png')
    output:
    tuple val(batch_id), path(expected_files), path('result/task.json')
    script:
    """
    python -m reprohpc.task analyze --spec-b64 '${spec_b64}' --output result image*.png
    """
}
