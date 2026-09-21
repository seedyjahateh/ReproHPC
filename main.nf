nextflow.enable.dsl=2

include { VALIDATE_DATASET } from './modules/local/validate'
include { ANALYZE_BATCH } from './modules/local/analyze_batch'
include { MRI_BATCH } from './modules/local/mri_batch'
include { AGGREGATE } from './modules/local/aggregate'
include { REPORT } from './modules/local/report'

workflow {
    // fsl-bet-volumetry-v1 replaces only the per-batch analysis step; validation,
    // aggregation, reporting and publication are the same processes for both routines.
    def mri = params.science.algorithm == 'fsl-bet-volumetry-v1'
    data_root = file(params.dataset).parent
    ref_root = file(params.reference).parent
    validated = VALIDATE_DATASET(data_root, ref_root, file(params.input_manifest), file(params.dataset).name, file(params.reference).name)
    batches = validated.map { checked ->
        def document = new groovy.json.JsonSlurper().parse(checked.toFile())
        document.samples.sort { it.sample_id }.collate(params.batch_size as int).withIndex().collect { group, idx ->
            def spec = [batch_id: String.format('batch-%06d',idx), samples:group, params:params.science,
                calibration:document.calibration, reference_sha256:params.reference_sha256, sif_sha256:params.sif_sha256]
            def encoded = groovy.json.JsonOutput.toJson(spec).bytes.encodeBase64().toString()
            def names = mri ? ['brain_mask.nii.gz', 'metrics.json']
                : ['mask.npy', 'objects.csv', 'metrics.json'] + (params.science.write_previews ? ['preview.png'] : [])
            def expected = group.collectMany { sample -> names.collect { name -> "result/samples/${sample.sample_id}/${name}".toString() } }
            tuple(spec.batch_id, encoded, expected, group.collect { file("${document.data_root}/${it.path}") })
        }
    }.flatMap()
    if (mri) {
        analyzed = MRI_BATCH(batches)
    } else {
        analyzed = ANALYZE_BATCH(batches)
    }
    batch_files = analyzed.map { id, scientific_files, metadata -> metadata.parent }.collect()
    summaries = AGGREGATE(validated, batch_files)
    REPORT(summaries, batch_files)
}

workflow.onComplete {
    def destination = new File(params.outdir as String, 'provenance/engine.json')
    destination.parentFile.mkdirs()
    destination.text = groovy.json.JsonOutput.toJson([session_id:workflow.sessionId.toString(),
        success:workflow.success, exit_status:workflow.exitStatus, run_name:workflow.runName,
        command_line:workflow.commandLine, nextflow_version:nextflow.version.toString()])
}
