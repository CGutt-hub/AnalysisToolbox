nextflow.enable.dsl=2

process NATIVE_MODULE {
    input:
        val env_exe          // Explicit, valid binary path
        val script           // Explicit, existing script path
        path(input_parquet)  // Staged input parquet file
        val plot_type        // Optional metadata tag
        val level_tag        // Must be explicitly 'L1' or 'L2'
        val extraParams      // Dynamic parameter string

    output:
        path("*.parquet"), emit: signal

    script:
        // ---------------------------------------------------------------------
        // CLASS LOADER FOR EXTERNAL PARSER
        // ---------------------------------------------------------------------
        def parserFile = moduleDir.resolve('../lib/argsParser.groovy').toFile()
        if (!parserFile.exists()) {
            throw new java.io.FileNotFoundException("[NATIVE_MODULE] CRITICAL: 'argsParser.groovy' missing at '${parserFile.absolutePath}'")
        }
        Class ArgsParser = new GroovyClassLoader(Thread.currentThread().getContextClassLoader()).parseClass(parserFile)

        // ---------------------------------------------------------------------
        // STRICT PARAMETER VALIDATION (FAIL FAST)
        // ---------------------------------------------------------------------
        if (env_exe == null || env_exe.toString().trim().isEmpty()) {
            throw new IllegalArgumentException("[NATIVE_MODULE] CRITICAL: 'env_exe' parameter is NULL or EMPTY.")
        }
        def activeEnvExe = env_exe.toString().trim()

        if (script == null || script.toString().trim().isEmpty()) {
            throw new IllegalArgumentException("[NATIVE_MODULE] CRITICAL: 'script' parameter is NULL or EMPTY.")
        }
        def cleanScript = script.toString().trim().replaceAll(/^["']|["']$/, '')

        def targetScriptFile = new java.io.File(cleanScript)
        if (!targetScriptFile.isAbsolute()) {
            targetScriptFile = workflow.projectDir.resolve(cleanScript).toFile()
        }

        if (!targetScriptFile.exists()) {
            throw new java.io.FileNotFoundException("[NATIVE_MODULE] CRITICAL: Target script file missing at '${targetScriptFile.absolutePath}'")
        }

        if (level_tag == null || level_tag.toString().trim().isEmpty()) {
            throw new IllegalArgumentException("[NATIVE_MODULE] CRITICAL: 'level_tag' parameter is mandatory (must be 'L1' or 'L2').")
        }
        def level = level_tag.toString().trim().toUpperCase()
        if (level != 'L1' && level != 'L2') {
            throw new IllegalArgumentException("[NATIVE_MODULE] CRITICAL: Invalid level_tag '${level}'. Must be 'L1' or 'L2'.")
        }

        def scriptName         = targetScriptFile.name
        def resolvedScriptPath = targetScriptFile.absolutePath
        def moduleTag          = scriptName.replaceAll(/(_processor|_analyzer)?\.[^.]+$/, '')

        // ---------------------------------------------------------------------
        // STRICT STEM & PATH DERIVATION
        // ---------------------------------------------------------------------
        def inputFileName = input_parquet.name
        def rawStem       = inputFileName.replaceAll(/\.[^.]+$/, '')
        def prefixMatcher = rawStem =~ /^([a-zA-Z0-9]+_[0-9]+)/

        if (!prefixMatcher.find()) {
            throw new IllegalArgumentException("[NATIVE_MODULE] CRITICAL: Input file '${inputFileName}' does not conform to expected prefix pattern ('<PROJECT>_<ID>').")
        }
        def filePrefix = prefixMatcher.group(1)

        def isL2 = (level == 'L2')
        def contextFolderName = isL2 ? "${params.project_name}_l2" : "${params.project_name}_${level.toLowerCase()}/${filePrefix}"
        def contextDirPath    = new java.io.File("${workflow.launchDir}/${params.output_dir}/${contextFolderName}/.bin").getCanonicalPath()
        def logBaseName       = isL2 ? "${params.project_name}_l2.log" : "${filePrefix}.log"
        def textLogPath       = "${contextDirPath}/${logBaseName}"

        // ---------------------------------------------------------------------
        // PARAMETER SANITIZATION (EXPLICIT CLOSURE PARAMETERS)
        // ---------------------------------------------------------------------
        def parsedParams = ArgsParser.parse(extraParams)
        def rawArgs      = parsedParams.extraArgsStr ?: ""
        
        def extraArgsString = rawArgs
            .replace('\\', '')
            .replaceAll(/''+/, "'")
            .replaceAll(/""+/, '"')
            .replaceAll(/\[\s*(.*?)\s*\]/) { String _fullMatch, String inner ->
                inner.replaceAll(/['"]/, "").split(',').collect { String item -> item.trim() }.join(',')
            }
            .trim()

        """
        #!/bin/bash
        set -e -o pipefail

        CONTEXT_DIR="${contextDirPath}"
        TXT_LOG="${textLogPath}"
        LOCK_FILE="\${TXT_LOG}.lock"
        MOD_TAG="${moduleTag}"
        LEVEL_TAG="${level}"
        EXEC_BIN="${activeEnvExe}"

        mkdir -p "\$CONTEXT_DIR"

        log_entry() {
            local state="\$1"
            local message="\$2"
            (
                flock -x 200
                printf "[%s] [%s] [%s] [NATIVE_MODULE] [%s] %s\\n" \
                    "\$(date '+%Y-%m-%d %H:%M:%S.%3N')" \
                    "\$LEVEL_TAG" \
                    "\$state" \
                    "\$MOD_TAG" \
                    "\$message" >> "\$TXT_LOG"
            ) 200>>"\$LOCK_FILE"
        }

        # FAIL FAST BASH CHECK: Binary existence and executable permission
        if [ ! -f "\$EXEC_BIN" ]; then
            log_entry "ERROR" "Python executable missing: '\$EXEC_BIN'"
            echo "FATAL: [NATIVE_MODULE] Binary path '\$EXEC_BIN' does not exist." >&2
            exit 1
        fi

        if [ ! -x "\$EXEC_BIN" ]; then
            log_entry "ERROR" "Python binary lacks execution permissions: '\$EXEC_BIN'"
            echo "FATAL: [NATIVE_MODULE] Binary '\$EXEC_BIN' is not executable." >&2
            exit 1
        fi

        log_entry "INFO" "Executing: \${EXEC_BIN} ${resolvedScriptPath} ${input_parquet} ${extraArgsString}"

        TEMP_STDERR=\$(mktemp)
        TEMP_STDOUT=\$(mktemp)

        set +e
        PYTHONUNBUFFERED=1 "\${EXEC_BIN}" "${resolvedScriptPath}" "${input_parquet}" ${extraArgsString} >"\$TEMP_STDOUT" 2>"\$TEMP_STDERR"
        EXIT_CODE=\$?
        set -e

        TS=\$(date '+%Y-%m-%d %H:%M:%S.%3N')

        # Stream stdout line-by-line as [INFO] (Stripping redundant internal Python module tags)
        if [ -s "\$TEMP_STDOUT" ]; then
            (
                flock -x 200
                awk -v ts="\$TS" -v lvl="\$LEVEL_TAG" -v mod="\$MOD_TAG" \
                    '{ sub(/^\\[[A-Za-z0-9_]+\\][ \\t]*(INFO|ERROR)?:[ \\t]*/, ""); print "[" ts "] [" lvl "] [INFO] [NATIVE_MODULE] [" mod "] " \$0 }' "\$TEMP_STDOUT" >> "\$TXT_LOG"
            ) 200>>"\$LOCK_FILE"
        fi

        # Stream stderr line-by-line as [ERROR] (Stripping redundant internal Python module tags)
        if [ -s "\$TEMP_STDERR" ]; then
            (
                flock -x 200
                awk -v ts="\$TS" -v lvl="\$LEVEL_TAG" -v mod="\$MOD_TAG" \
                    '{ sub(/^\\[[A-Za-z0-9_]+\\][ \\t]*(INFO|ERROR)?:[ \\t]*/, ""); print "[" ts "] [" lvl "] [ERROR] [NATIVE_MODULE] [" mod "] " \$0 }' "\$TEMP_STDERR" >> "\$TXT_LOG"
            ) 200>>"\$LOCK_FILE"
        fi

        # FAIL FAST: Forward script error trace directly to stderr
        if [ \$EXIT_CODE -ne 0 ]; then
            log_entry "ERROR" "Script returned error code \$EXIT_CODE."
            echo "FATAL: [NATIVE_MODULE] Execution failed in script '${scriptName}'" >&2
            cat "\$TEMP_STDERR" >&2
            rm -f "\$TEMP_STDOUT" "\$TEMP_STDERR"
            exit \$EXIT_CODE
        fi

        # Extract targeted output file path from stdout
        GENERATED_FILE=\$(tail -n 1 "\$TEMP_STDOUT" | tr -d '\\r\\n')
        rm -f "\$TEMP_STDOUT" "\$TEMP_STDERR"

        # FAIL FAST: Validate output existence explicitly
        if [ -z "\$GENERATED_FILE" ] || [ ! -f "\$GENERATED_FILE" ]; then
            log_entry "ERROR" "Module exited 0 but produced no valid output path. Output string: '\$GENERATED_FILE'"
            echo "FATAL: [NATIVE_MODULE] '${scriptName}' failed to generate valid Parquet output: '\$GENERATED_FILE'" >&2
            exit 1
        fi

        # METADATA INJECTION VIA EMBEDDED DUCKDB IN PYTHON BINARY
        PLOT_TYPE_VAL="${plot_type ?: ''}"
        if [ -n "\$PLOT_TYPE_VAL" ]; then
            if ! "\$EXEC_BIN" -c "import duckdb" >/dev/null 2>&1; then
                log_entry "ERROR" "Python environment '\$EXEC_BIN' is missing required 'duckdb' package."
                echo "FATAL: [NATIVE_MODULE] Python package 'duckdb' required for metadata injection but missing in \$EXEC_BIN." >&2
                exit 1
            fi

            TEMP_META_PARQUET=\$(mktemp --suffix=.parquet)
            
            "\$EXEC_BIN" -c "
import duckdb
conn = duckdb.connect()
conn.execute('''
    COPY (SELECT * FROM read_parquet('\$GENERATED_FILE')) 
    TO '\$TEMP_META_PARQUET' 
    (FORMAT PARQUET, COMPRESSION 'ZSTD', KV_METADATA {'plot_type': '\$PLOT_TYPE_VAL'})
''')
"
            mv "\$TEMP_META_PARQUET" "\$GENERATED_FILE"
            log_entry "INFO" "Injected plot_type metadata via Python DuckDB (ZSTD)."
        fi

        # FIX: Extract filename from path to prevent invalid nested absolute path copy
        OUTPUT_FILENAME=\$(basename "\$GENERATED_FILE")
        cp "\$GENERATED_FILE" "\$CONTEXT_DIR/\$OUTPUT_FILENAME"
        log_entry "INFO" "Procedure output stored in: \$CONTEXT_DIR/\$OUTPUT_FILENAME"
        """
}