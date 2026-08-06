// processes/NATIVE_CHANNEL_L2.nf
nextflow.enable.dsl=2

process NATIVE_CHANNEL_L2 {

    // Enforces serial DuckDB concatenation to prevent write locks on EV2_binned_*.parquet
    maxForks 1

    publishDir (
        path: { "${params.output_dir}/${params.l2_folder}" },
        mode: 'copy',
        pattern: "*.parquet"
    )

    input:
        path registry_parquet
        val  incoming_signal_sample

    output:
        tuple val(signal_suffix), path("*.parquet"), emit: cohort_matrix

    exec:
    // ---------------------------------------------------------------------
    // STRICT PARAMETER VALIDATION (FAIL FAST - ZERO FALLBACKS)
    // ---------------------------------------------------------------------
    if (params.output_dir == null || params.output_dir.toString().trim().isEmpty()) {
        throw new IllegalArgumentException("[NATIVE_CHANNEL_L2] CRITICAL: Mandatory parameter 'params.output_dir' is NULL or EMPTY.")
    }
    if (params.project_name == null || params.project_name.toString().trim().isEmpty()) {
        throw new IllegalArgumentException("[NATIVE_CHANNEL_L2] CRITICAL: Mandatory parameter 'params.project_name' is NULL or EMPTY.")
    }
    if (params.l2_folder == null || params.l2_folder.toString().trim().isEmpty()) {
        throw new IllegalArgumentException("[NATIVE_CHANNEL_L2] CRITICAL: Mandatory parameter 'params.l2_folder' is NULL or EMPTY.")
    }
    if (incoming_signal_sample == null) {
        throw new IllegalArgumentException("[NATIVE_CHANNEL_L2] CRITICAL: Input signal parameter 'incoming_signal_sample' is NULL.")
    }

    def outputDirVal   = params.output_dir.toString().trim()
    def projectNameVal = params.project_name.toString().trim()
    def l2FolderVal    = params.l2_folder.toString().trim()

    def launchDir = workflow.launchDir.toFile()
    def l2BinDir  = new java.io.File(launchDir, "${outputDirVal}/${l2FolderVal}/.bin")

    if (!l2BinDir.exists() && !l2BinDir.mkdirs()) {
        throw new java.io.IOException("[NATIVE_CHANNEL_L2] CRITICAL: Failed to create bin directory at '${l2BinDir.absolutePath}'.")
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

    def pathUtilsFile = [
        moduleDir.resolve('../lib/pathUtils.groovy').toFile(),
        moduleDir.resolve('../../lib/pathUtils.groovy').toFile()
    ].find { java.io.File candidateFile -> candidateFile.exists() }

    if (!sqlUtilsFile || !baseFsFile || !pathUtilsFile) {
        throw new java.io.FileNotFoundException("[NATIVE_CHANNEL_L2] CRITICAL: Shared framework utility classes missing from classpath.")
    }

    gcl.addClasspath(sqlUtilsFile.parentFile.absolutePath)
    def SqlUtils            = gcl.parseClass(sqlUtilsFile)
    def BaseFileSystemUtils = gcl.parseClass(baseFsFile)
    def PathUtils           = gcl.parseClass(pathUtilsFile)

    // ---------------------------------------------------------------------
    // 1. SIGNAL PATTERN RESOLUTION (USING PathUtils)
    // ---------------------------------------------------------------------
    def extractedPaths = PathUtils.extractParquetPaths(incoming_signal_sample)
    if (extractedPaths.isEmpty()) {
        throw new IllegalArgumentException("[NATIVE_CHANNEL_L2] CRITICAL: Unable to extract valid Parquet paths from incoming signal sample.")
    }

    def sampleFileName = new java.io.File(extractedPaths.last()).name
    
    // Strip ID prefix (e.g. "EV2_P01_fai-amplitude-rmssd.parquet" -> "fai-amplitude-rmssd")
    // Explicitly assigned to signal_suffix matching output signature
    signal_suffix = sampleFileName.replaceFirst(/^[^_]+_/, '')
                                  .replaceAll(/\.parquet$/, '')
                                  .replaceFirst(/^[^_]+_/, '')

    def cleanPatternName = signal_suffix.endsWith('.parquet') ? signal_suffix : "${signal_suffix}.parquet"

    BaseFileSystemUtils.appendLog(mainLog, "[L2] [INFO] [NATIVE_CHANNEL_L2] Initiating cohort matrix concatenation for pattern: '*_${cleanPatternName}'")

    // ---------------------------------------------------------------------
    // 2. READ ACTIVE PARTICIPANTS VIA SqlUtils
    // ---------------------------------------------------------------------
    def escRegistry  = SqlUtils.escapePath(registry_parquet.toAbsolutePath().toString())
    def registryRows = SqlUtils.executeSelectQuery("SELECT participant_id, input_directory FROM read_parquet('${escRegistry}')")

    if (registryRows.isEmpty()) {
        def errMsg = "Participant registry at '${escRegistry}' is empty."
        BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [NATIVE_CHANNEL_L2] ${errMsg}")
        throw new RuntimeException("[NATIVE_CHANNEL_L2] CRITICAL: ${errMsg}")
    }

    def participantEntries = registryRows.collect { Map<String, Object> row ->
        return [
            id: row.get("participant_id").toString(),
            dir: row.get("input_directory").toString()
        ]
    }

    // ---------------------------------------------------------------------
    // 3. SCAN & VERIFY PARTICIPANT L1 SIGNAL FILES
    // ---------------------------------------------------------------------
    def validatedFiles = participantEntries.collect { Map entry ->
        def pId  = entry.id
        def pDir = entry.dir.startsWith('/') ? new java.io.File(entry.dir) : new java.io.File(launchDir, entry.dir)

        if (!pDir.exists() || !pDir.isDirectory()) {
            def errMsg = "L1 folder missing for participant '${pId}' at: ${pDir.absolutePath}"
            BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [NATIVE_CHANNEL_L2] ${errMsg}")
            throw new java.io.FileNotFoundException("[NATIVE_CHANNEL_L2] CRITICAL: ${errMsg}")
        }

        def matchedFile = pDir.listFiles().find { java.io.File diskFile ->
            diskFile.isFile() && diskFile.name.endsWith(".parquet") && diskFile.name.endsWith(cleanPatternName)
        }

        if (!matchedFile || !matchedFile.exists() || matchedFile.length() <= 12) {
            def errMsg = "Missing or corrupt signal file matching '*_${cleanPatternName}' for participant '${pId}' in ${pDir.absolutePath}"
            BaseFileSystemUtils.appendLog(mainLog, "[L2] [ERROR] [NATIVE_CHANNEL_L2] ${errMsg}")
            throw new RuntimeException("[NATIVE_CHANNEL_L2] CRITICAL: ${errMsg}")
        }

        return [pid: pId, path: matchedFile.absolutePath]
    }

    // ---------------------------------------------------------------------
    // 4. DUCKDB ROW CONCATENATION (UNION ALL)
    // ---------------------------------------------------------------------
    def outputFileName = "${projectNameVal}_binned_${signal_suffix}.parquet"
    def localDest      = new java.io.File(task.workDir.toFile(), outputFileName)

    SqlUtils.withConnection { java.sql.Connection conn ->
        def unionQueries = validatedFiles.collect { Map item ->
            def escFp = SqlUtils.escapePath(item.path.toString())
            def escId = SqlUtils.escapeSql(item.pid.toString())
            return "SELECT '${escId}' AS participant_id, * FROM read_parquet('${escFp}')"
        }

        def stackedQuery = unionQueries.join(" UNION ALL ")
        def escDest      = SqlUtils.escapePath(localDest.absolutePath)
        def query        = "COPY (${stackedQuery}) TO '${escDest}' (FORMAT PARQUET, COMPRESSION 'ZSTD')"

        SqlUtils.executeQuery(conn, query)
    }

    // ---------------------------------------------------------------------
    // 5. MIRROR OUTPUT FILE TO COHORT BIN DIRECTORY
    // ---------------------------------------------------------------------
    def outputBinDest = new java.io.File(l2BinDir, outputFileName)
    java.nio.file.Files.copy(localDest.toPath(), outputBinDest.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)

    BaseFileSystemUtils.appendLog(mainLog, "[L2] [INFO] [NATIVE_CHANNEL_L2] Successfully created cohort matrix '${outputFileName}' across ${validatedFiles.size()} participant(s).")
}