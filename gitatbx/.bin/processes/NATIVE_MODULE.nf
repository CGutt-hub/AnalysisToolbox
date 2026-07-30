// =========================================================================
// NATIVE MODULE PROCESS (GROOVY 3+ & DSL2 COMPLIANT)
// =========================================================================

process NATIVE_MODULE {
    errorStrategy 'ignore'
    
    publishDir (
        path: { _pathObj ->
            def level = level_tag?.toString()?.toLowerCase()?.trim() ?: 'l1'
            if (level == 'l2') {
                return "${params.output_dir}/${params.project_name}_l2/.bin"
            } else {
                return "${params.output_dir}/${params.project_name}_${level}/${id}/.bin"
            }
        },
        mode: 'copy',
        pattern: "*.parquet"
    )
    
    input:
        val env_exe
        val script
        tuple val(id), path(input_parquet)
        val plot_type
        val level_tag              // Input 5: Explicit "l1", "l2", or custom level tag
        val extraParams            // Input 6: Pure Python CLI parameters string

    output:
        tuple val(id), path({ _pathObj ->
            def moduleUtils = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
                .parseClass(moduleDir.resolve('../lib/ModuleUtils.groovy').toFile())
            "${moduleUtils.resolveName(id, input_parquet.name, script)}.parquet"
        }), emit: signal

    script:
    def moduleUtils = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
        .parseClass(moduleDir.resolve('../lib/ModuleUtils.groovy').toFile())
    
    def output_filename = moduleUtils.resolveName(id, input_parquet.name, script)
    def level = level_tag?.toString()?.toLowerCase()?.trim() ?: 'l1'
    def isL2  = (level == 'l2')

    // Groovy 3+ Clean Tokenizer & Parameter Escaping
    def paramStr = extraParams?.toString()?.trim() ?: ''
    def cleanTokens = []
    if (!paramStr.isEmpty() && !paramStr.equalsIgnoreCase('none') && !paramStr.equalsIgnoreCase('null')) {
        if (extraParams instanceof Collection) {
            cleanTokens = extraParams.flatten().collect { item -> item?.toString()?.trim() ?: '' }.findAll { token -> token && token != 'terminal' }
        } else {
            cleanTokens = paramStr.tokenize(' ').collect { tokenItem -> tokenItem.trim() }.findAll { tokenItem -> tokenItem && tokenItem != 'terminal' }
        }
    }

    def extraArgsString = cleanTokens.collect { argToken ->
        def tokenStr = argToken.toString()
        if ((tokenStr.startsWith("'") && tokenStr.endsWith("'")) || (tokenStr.startsWith('"') && tokenStr.endsWith('"'))) {
            return tokenStr
        }
        return "'${tokenStr.replace("'", "'\\''")}'"
    }.join(' ')

    // Context & Directory Resolution
    def contextFolderName = isL2 ? "${params.project_name}_l2" : "${params.project_name}_${level}/${id}"
    def contextDirPath    = "${workflow.launchDir}/${params.output_dir}/${contextFolderName}/.bin"
    def logBaseName       = isL2 ? "${params.project_name}_l2.log" : "${id}.log"
    def textLogPath       = "${contextDirPath}/${logBaseName}"
    def scriptName        = script.toString().tokenize('/').last().replace('.py', '')

    """
    #!/bin/bash
    set -e -o pipefail
    
    CONTEXT_DIR="${contextDirPath}"
    TXT_LOG="${textLogPath}"
    LOCK_FILE="\${TXT_LOG}.lock"

    mkdir -p "\$CONTEXT_DIR"

    log_msg() {
        local level_lvl="\$1"
        local message="\$2"
        (
            flock -x 200
            printf "%s [%s] [%s] %s\\n" "\$(date '+%Y-%m-%d %H:%M:%S')" "${level.toUpperCase()}" "\$level_lvl" "\$message" >> "\$TXT_LOG"
        ) 200>>"\$LOCK_FILE"
    }

    if [ ! -f "\$TXT_LOG" ]; then
        (
            flock -x 200
            if [ ! -f "\$TXT_LOG" ]; then
                printf "=== ${params.project_name} [${level.toUpperCase()}] log: %s ===\\nWorkflow: ${workflow.projectDir}\\nSession:  ${workflow.sessionId}\\nContext:  %s\\n\\n" \\
                    "\$(date '+%Y-%m-%d %H:%M:%S')" "\$CONTEXT_DIR" > "\$TXT_LOG"
            fi
        ) 200>>"\$LOCK_FILE"
    fi

    log_msg "INFO" "${scriptName} - Processing started."

    LOCAL_TARGET_PARQUET="${input_parquet.name}"
    FINAL_OUTPUT="${output_filename}.parquet"

    # Integrity Check (Fail-Fast)
    if [ ! -f "\$LOCAL_TARGET_PARQUET" ]; then
        log_msg "ERROR" "Input file \$LOCAL_TARGET_PARQUET missing."
        exit 1
    fi

    FILE_SIZE=\$(stat -c %s "\$LOCAL_TARGET_PARQUET" 2>/dev/null || stat -f %z "\$LOCAL_TARGET_PARQUET" 2>/dev/null || echo 0)
    if [ "\$FILE_SIZE" -le 12 ]; then
        log_msg "ERROR" "Input file \$LOCAL_TARGET_PARQUET is invalid (\${FILE_SIZE} bytes)."
        exit 1
    fi

    # Execute Sub-Module Script
    TEMP_OUT=\$(mktemp)
    export VIS_LABEL_MAP='${params.vis_label_map ?: '{}'}'
    
    CMD="${env_exe} -u \\"${workflow.launchDir}/${script}\\" \\"\$LOCAL_TARGET_PARQUET\\" ${extraArgsString}"
    
    set +e
    eval "\$CMD" 2>&1 | tee "\$TEMP_OUT"
    EXIT_CODE=\${PIPESTATUS[0]}
    set -e

    if [ -s "\$TEMP_OUT" ]; then
        TS=\$(date '+%Y-%m-%d %H:%M:%S')
        (
            flock -x 200
            awk -v ts="\$TS" '{print ts " " \$0}' "\$TEMP_OUT" >> "\$TXT_LOG"
        ) 200>>"\$LOCK_FILE"
    fi
    rm -f "\$TEMP_OUT"

    if [ \$EXIT_CODE -ne 0 ]; then
        log_msg "ERROR" "${scriptName} failed with exit code \$EXIT_CODE."
        exit \$EXIT_CODE
    fi

    # Output Resolution & Renaming
    GENERATED_FILE=\$(ls *.parquet 2>/dev/null | grep -v -x "\$LOCAL_TARGET_PARQUET" | head -n 1 || echo "")
    
    if [ -n "\$GENERATED_FILE" ] && [ -f "\$GENERATED_FILE" ]; then
        if [ "\$GENERATED_FILE" != "\$FINAL_OUTPUT" ]; then
            mv "\$GENERATED_FILE" "\$FINAL_OUTPUT"
        fi
    else
        log_msg "ERROR" "${scriptName} completed with exit code 0 but failed to generate a new Parquet asset."
        exit 1
    fi

    # Metadata Injection
    ${env_exe} -c "
import os, pyarrow.parquet as pq
p = '\$FINAL_OUTPUT'
if os.path.exists(p) and os.path.getsize(p) > 12:
    try:
        schema = pq.read_schema(p)
        meta = dict(schema.metadata or {})
        meta[b'plot_type'] = '${plot_type}'.encode('utf-8')
        table = pq.read_table(p)
        pq.write_table(table.replace_schema_metadata(meta), p)
    except Exception:
        pass
" 2>/dev/null || true

    log_msg "INFO" "Module execution complete. Asset created: \$FINAL_OUTPUT"
    """
}