// =========================================================================
// TRULY NATIVE JOIN PROCESS (Singular Standalone Process Block - PRODUCTION SAFE)
// =========================================================================
process NATIVE_JOIN {
    tag "Join_Execution"
    
    publishDir (
        path: { 
            // 💡 PARTICIPANT AGNOSTIC FIX: Stops all array string splitting crashes inside publishDir!
            // Let Nextflow copy files cleanly to the shared level 1 tables subdirectory context.
            "${params.output_dir}/${params.project_name}_l1/${task.ext.routing_folder ?: 'tables'}" 
        },
        mode: 'copy',
        pattern: "*.parquet"
    )

    input:
        val incoming_signals

    output:
        tuple val("group"), path("*.parquet"), emit: merged_matrix

    script:
        def loader = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
        def mixParserClass = loader.parseClass(new File(moduleDir.toString(), "utils/mixParser.groovy"))

        // Extract clean physical files without any forbidden closures
        def cleanFilePaths = incoming_signals != null ? mixParserClass.extractCleanPaths(incoming_signals) : []

        // Resolve the exact parent base name stem using your class helper
        def rawPrimaryFile = cleanFilePaths.isEmpty() ? "" : new File(cleanFilePaths.get(0).toString()).name
        resolvedBaseName   = mixParserClass.resolveHierarchicalName("", "", rawPrimaryFile)
        
        def fileArgumentsStr = cleanFilePaths.collect { String fileObj -> "'${fileObj.toString()}'" }.join(' ')

    // === PURE AGNOSTIC BASH SUB-SHELL ===
    """
    #!/bin/bash
    set -e

    # 💡 STEPWISE APPRENDING: Simply appends _join to the untouched base name!
    TARGET_FILE="${resolvedBaseName}_join.parquet"

    if [ -z "${fileArgumentsStr}" ] || [ "${resolvedBaseName}" = "empty" ]; then
        echo "[Native Join] WARNING: Zero active vectors. Seeding empty matrix schema..."
        ${params.python_exe} -c "import polars as pl; pl.DataFrame(schema={'condition': pl.String, 'epoch_id': pl.Int64}).write_parquet('\$TARGET_FILE')"
        exit 0
    fi

    cat ${fileArgumentsStr} > "\$TARGET_FILE"
    echo "[Native Join] Successfully created base-appended matrix: \$TARGET_FILE"
    """
}