process FINALIZE_L1 {

    input:
        tuple val(participant_id), path(root_signals), path(sync_triggers)

    output:
        tuple val(participant_id), val("${params.output_dir}/${params.project_name}_l1/${participant_id}")

    exec:
    def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
    gcl.addClasspath(moduleDir.resolve('../').toFile().absolutePath)
    
    def FinalizationUtils = gcl.parseClass(moduleDir.resolve('../lib/finalizationUtils.groovy').toFile())

    def participantId = participant_id.toString()
    def targetPath    = "${params.output_dir}/${params.project_name}_l1/${participantId}"

    FinalizationUtils.finalizeFiles(
        workflow, 
        params, 
        moduleDir, 
        participantId, 
        targetPath, 
        root_signals, 
        sync_triggers, 
        "L1", 
        "L1 finalized for participant: ${participantId}"
    )
}