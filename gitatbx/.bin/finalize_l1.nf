// =========================================================================
// STANDARD-WORKFLOW-GATEWAY: LEVEL 1 FINALIZATION (EXPORT-MODUL)
// =========================================================================
workflow finalize_l1 {
    take:
        l1_channel_list
        result_count
        _participant_context // Führender Unterstrich unterdrückt Linter-Warnung

    main:
        def loader = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
        
        def mixParserClass = loader.parseClass(new File(moduleDir.toString(), "utils/mixParser.groovy"))
        def mapParserClass = loader.parseClass(new File(moduleDir.toString(), "utils/mapParser.groovy"))
        def gitSyncClass   = loader.parseClass(new File(moduleDir.toString(), "utils/gitSync.groovy"))

        def mixed_signals = mixParserClass.mixChannelList(l1_channel_list)

        mixed_signals
            .map { file_obj -> 
                def matcher = (file_obj.name =~ /(EV2_\d+)/)
                def pid = matcher.find() ? matcher.group(1) : file_obj.baseName.toString().split('_')[0..1].join('_')
                return [pid, file_obj] 
            }
            .groupTuple(size: result_count, remainder: true)
            .collect() 
            .subscribe { tuple_list ->
                // Reine serielle Schleife ohne asynchrone Map-Zustände im Gatter-Scope
                tuple_list.each { pid, files_list ->
                    if (pid && files_list) {
                        def valid_files = files_list.findAll { f -> f != null }
                        def targetFolder = "${workflow.launchDir}/${params.output_dir}/${params.project_name}_l1/${pid}"
                        
                        mapParserClass.exportCohortFiles(valid_files, targetFolder)
                        gitSyncClass.syncRepository(workflow, params, "L1 final sync completed for subject ${pid}")
                    }
                }
            }
}
