process FINALIZE_L2 {

    input:
        path root_signals
        path sync_triggers

    exec:
    def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
    gcl.addClasspath(moduleDir.resolve('../').toFile().absolutePath)
    
    def FinalizationUtils = gcl.parseClass(moduleDir.resolve('../lib/finalizationUtils.groovy').toFile())

    def l2FolderName = params.l2_folder ?: "${params.project_name}_l2"
    def targetPath   = "${params.output_dir}/${l2FolderName}"

    FinalizationUtils.finalizeFiles(
        workflow, 
        params, 
        moduleDir, 
        null, 
        targetPath, 
        root_signals, 
        sync_triggers, 
        "L2", 
        "L2 finalized"
    )
}