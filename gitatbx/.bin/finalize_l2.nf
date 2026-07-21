// =========================================================================
// STANDARD-WORKFLOW-GATEWAY: LEVEL 2 FINALIZATION (EXPORT-MODUL)
// =========================================================================
workflow finalize_l2 {
    take:
        l2_channel_list

    main:
        def loader = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
        
        def mixParserClass = loader.parseClass(new File(moduleDir.toString(), "utils/mixParser.groovy"))
        def mapParserClass = loader.parseClass(new File(moduleDir.toString(), "utils/mapParser.groovy"))
        def gitSyncClass   = loader.parseClass(new File(moduleDir.toString(), "utils/gitSync.groovy"))

        def mixed_signals = mixParserClass.mixChannelList(l2_channel_list)

        mixed_signals
            .collect()
            .subscribe { files_list ->
                def valid_files = files_list.findAll { f -> f != null }
                def targetFolder = "${workflow.launchDir}/${params.output_dir}/${params.l2_folder ?: 'l2_analysis'}"
                
                mapParserClass.exportCohortFiles(valid_files, targetFolder)
                gitSyncClass.syncRepository(workflow, params, "L2 final group-level sync completed successfully.")
            }
}
