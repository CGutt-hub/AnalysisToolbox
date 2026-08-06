// workflows/data_discovery_l2.nf
nextflow.enable.dsl=2

process DATA_DISCOVERY_L2 {

    // Enforces strict FIFO serialization so dynamic participant triggers execute sequentially
    maxForks 1

    publishDir (
        path: { "${params.output_dir}/${params.l2_folder}/.bin" },
        mode: 'copy',
        pattern: "*.parquet"
    )

    input:
        val single_l1_trigger // Signal emitted per completed participant from FINALIZE_L1

    output:
        path("l2_participant_registry.parquet"), emit: registry

    exec:
    // ---------------------------------------------------------------------
    // STRICT PARAMETER VALIDATION (FAIL FAST - ZERO FALLBACKS / PLACEHOLDERS)
    // ---------------------------------------------------------------------
    if (params.output_dir == null || params.output_dir.toString().trim().isEmpty()) {
        throw new IllegalArgumentException("[DATA_DISCOVERY_L2] CRITICAL: Mandatory parameter 'params.output_dir' is NULL or EMPTY.")
    }
    if (params.project_name == null || params.project_name.toString().trim().isEmpty()) {
        throw new IllegalArgumentException("[DATA_DISCOVERY_L2] CRITICAL: Mandatory parameter 'params.project_name' is NULL or EMPTY.")
    }
    if (params.l2_folder == null || params.l2_folder.toString().trim().isEmpty()) {
        throw new IllegalArgumentException("[DATA_DISCOVERY_L2] CRITICAL: Mandatory parameter 'params.l2_folder' is NULL or EMPTY.")
    }
    if (single_l1_trigger == null) {
        throw new IllegalArgumentException("[DATA_DISCOVERY_L2] CRITICAL: Input signal 'single_l1_trigger' is NULL.")
    }

    def outputDirVal   = params.output_dir.toString().trim()
    def projectNameVal = params.project_name.toString().trim()
    def l2FolderVal    = params.l2_folder.toString().trim()

    def launchDir = workflow.launchDir.toFile()
    def l1Dir     = new java.io.File(launchDir, "${outputDirVal}/${projectNameVal}_l1")
    def l2BinDir  = new java.io.File(launchDir, "${outputDirVal}/${l2FolderVal}/.bin")

    if (!l2BinDir.exists() && !l2BinDir.mkdirs()) {
        throw new java.io.IOException("[DATA_DISCOVERY_L2] CRITICAL: Failed to create directory structure at '${l2BinDir.absolutePath}'.")
    }

    def mainLog = new java.io.File(l2BinDir, "${projectNameVal}_l2.log")

    // ---------------------------------------------------------------------
    // DYNAMIC CLASS LOADING FOR UTILITIES
    // ---------------------------------------------------------------------
    def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
    
    def sqlUtilsFile = [
        moduleDir.resolve('../lib/SqlUtils.groovy').toFile(),
        moduleDir.resolve('../../lib/SqlUtils.groovy').toFile()
    ].find { java.io.File candidateFile -> candidateFile.exists() }

    def baseFsFile = [
        moduleDir.resolve('../lib/base/BaseFileSystemUtils.groovy').toFile(),
        moduleDir.resolve('../../lib/base/BaseFileSystemUtils.groovy').toFile()
    ].find { java.io.File candidateFile -> candidateFile.exists() }

    def baseIdFile = [
        moduleDir.resolve('../lib/base/BaseIdentifierUtils.groovy').toFile(),
        moduleDir.resolve('../../lib/base/BaseIdentifierUtils.groovy').toFile()
    ].find { java.io.File candidateFile -> candidateFile.exists() }

    if (!sqlUtilsFile || !baseFsFile || !baseIdFile) {
        throw new java.io.FileNotFoundException("[DATA_DISCOVERY_L2] CRITICAL: Shared framework utility classes missing from classpath.")
    }

    gcl.addClasspath(sqlUtilsFile.parentFile.absolutePath)
    def SqlUtils            = gcl.parseClass(sqlUtilsFile)
    def BaseFileSystemUtils = gcl.parseClass(baseFsFile)
    def BaseIdentifierUtils = gcl.parseClass(baseIdFile)

    if (!l1Dir.exists() || !l1Dir.isDirectory()) {
        def errMsg = "L1 output directory missing or unreadable at '${l1Dir.absolutePath}'"
        BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [DATA_DISCOVERY_L2] ${errMsg}")
        throw new java.io.FileNotFoundException("[DATA_DISCOVERY_L2] CRITICAL: ${errMsg}")
    }

    // ---------------------------------------------------------------------
    // DISCOVERY & FAIL-FAST CHECKS (USING BaseIdentifierUtils)
    // ---------------------------------------------------------------------
    def patternRegex = BaseIdentifierUtils.globToRegex(params.participant_pattern ? params.participant_pattern.toString().trim() : "")
    def participantDirs = l1Dir.listFiles().findAll { java.io.File diskFile ->
        diskFile.isDirectory() && !diskFile.name.startsWith('.') && diskFile.name.matches(patternRegex)
    }.sort { java.io.File dirFile -> dirFile.name }

    if (participantDirs.isEmpty()) {
        def errMsg = "No valid participant folders found in '${l1Dir.absolutePath}' matching pattern '${patternRegex}'"
        BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [DATA_DISCOVERY_L2] ${errMsg}")
        throw new RuntimeException("[DATA_DISCOVERY_L2] CRITICAL: ${errMsg}")
    }

    def participantTuples = participantDirs.collect { java.io.File participantFolder ->
        def pId   = participantFolder.name
        def pPath = "${outputDirVal}/${projectNameVal}_l1/${pId}"
        return [pId, pPath]
    }

    // BaseFileSystemUtils.appendLog automatically prepends standard timestamp [YYYY-MM-DD HH:mm:ss.SSS]
    BaseFileSystemUtils.appendLog(mainLog, "[L2] [INFO] [DATA_DISCOVERY_L2] Trigger received. Rescanned disk and discovered ${participantTuples.size()} participant directory(ies).")

    // ---------------------------------------------------------------------
    // PARQUET REGISTRY GENERATION VIA DUCKDB
    // ---------------------------------------------------------------------
    def localDest = new java.io.File(task.workDir.toFile(), "l2_participant_registry.parquet")

    def valueRows = participantTuples.collect { List tupleItem ->
        def pId  = SqlUtils.escapeSql(tupleItem[0].toString().trim())
        def pDir = SqlUtils.escapeSql(tupleItem[1].toString().trim())
        return "('${pId}', '${pDir}')"
    }.join(", ")

    SqlUtils.withConnection { java.sql.Connection conn ->
        def escDest   = SqlUtils.escapePath(localDest.absolutePath)
        def copyQuery = """
            COPY (
                SELECT column0 AS participant_id, column1 AS input_directory 
                FROM (VALUES ${valueRows})
            ) TO '${escDest}' (FORMAT PARQUET, COMPRESSION 'ZSTD')
        """
        SqlUtils.executeQuery(conn, copyQuery)
    }

    def registryBinDest = new java.io.File(l2BinDir, "l2_participant_registry.parquet")
    java.nio.file.Files.copy(localDest.toPath(), registryBinDest.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)

    BaseFileSystemUtils.appendLog(mainLog, "[L2] [INFO] [DATA_DISCOVERY_L2] Successfully generated and published L2 registry Parquet file with ${participantTuples.size()} participant record(s).")
}

workflow data_discovery_l2 {
    take:
        l1_finalized_trigger // Channel emitting participant signals from FINALIZE_L1

    main:
        l2_registry = DATA_DISCOVERY_L2( l1_finalized_trigger )

    emit:
        registry = l2_registry.registry
}