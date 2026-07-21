// =========================================================================
// NATIVE MODULE PROCESS (Kern-Ausführungsknoten - HIERARCHICAL NAMING SECURE)
// =========================================================================
process NATIVE_MODULE {
    tag "${id}"
    
    publishDir (
        path: { 
            def isGroupLog = extraParams.toString().trim().contains("group_log")
            isGroupLog 
                ? "${params.output_dir}/${params.project_name}_l2/${artifact_type}" 
                : "${params.output_dir}/${params.project_name}_l1/${id}/${artifact_type}"
        },
        mode: 'copy',
        pattern: "*.parquet"
    )
    
    input:
        val env_exe             
        val script              
        tuple val(artifact_type), val(plot_type), val(extraParams) 
        tuple val(id), path(input_parquet) 

    output:
        path "*.{fif,parquet}", optional: true
        tuple val(id), val(artifact_type), val(plot_type), path("${resolvedBaseName}.parquet"), emit: signal

    script:
    def loader = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
    def mixParserClass = loader.parseClass(new File(moduleDir.toString(), "utils/mixParser.groovy"))

    // Resolve 'EV2_011_amplitude' cleanly at the process boundary
    resolvedBaseName = mixParserClass.resolveHierarchicalName(id.toString(), artifact_type.toString(), input_parquet.toString())

    def paramStr = extraParams.toString().trim()
    def rawTokens = (paramStr =~ /('[^']*'|"[^"]*"|\[[^\]]*\]|\{[^\}]*\}|\S+)/).findAll().collect { List m -> m.toString() }
    def isGroupLog = rawTokens.contains('group_log')
    def cleanTokens = rawTokens.findAll { t -> t != 'group_log' && t != 'terminal' }
    def extraArgsString = cleanTokens.collect { t -> 
        if ((t.startsWith("'") && t.endsWith("'")) || (t.startsWith('"') && t.endsWith('"'))) { return t }
        return "'${t.replace("'", "'\\''")}'"
    }.join(' ')

    def groupFolderName = "${params.project_name}_l2"
    def groupDir        = "${workflow.launchDir}/${params.output_dir}/${groupFolderName}"
    def contextDirPath  = isGroupLog ? groupDir : "${workflow.launchDir}/${params.output_dir}/${params.project_name}_l1/${id}"
    def logBaseName     = isGroupLog ? "${groupFolderName}.log" : "${id}.log"
    def textLogPath     = "${contextDirPath}/${logBaseName}.txt"
    def parquetLogPath  = "${contextDirPath}/${logBaseName}.parquet"
    def scriptName      = script.toString().tokenize('/').last().replace('.py', '')

    """
    #!/bin/bash
    set -e
    
    CONTEXT_DIR="${contextDirPath}"
    TXT_LOG="${textLogPath}"
    PARQUET_LOG="${parquetLogPath}"

    _sync_to_parquet() {
        ${env_exe} -c "
import polars as pl, os
txt_p, pq_p = '\$TXT_LOG', '\$PARQUET_LOG'
if os.path.exists(txt_p):
    with open(txt_p, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    pl.DataFrame({'content': [content]}).write_parquet(pq_p, compression='gzip')
" 2>/dev/null || true
    }

    mkdir -p "\$CONTEXT_DIR"

    if [ ! -f "\$TXT_LOG" ]; then
        printf "=== ${params.project_name} log: %s ===\\nWorkflow: ${workflow.projectDir}\\nSession:  ${workflow.sessionId}\\nOutput:   %s\\n\\n" \\
            "\$(date '+%Y-%m-%d %H:%M:%S')" "\$CONTEXT_DIR" > "\$TXT_LOG"
    fi

    printf "%s [INFO] ${scriptName} - Agnostic processing initiated.\\n" "\$(date '+%Y-%m-%d %H:%M:%S')" >> "\$TXT_LOG"
    _sync_to_parquet

    LOCAL_TARGET_PARQUET=\$(basename "${input_parquet}")

    # --- SCHEMA-HARDENED INPUT DEFENSIVE GUARD ---
    if [ ! -f "\$LOCAL_TARGET_PARQUET" ] || [ \$(stat -c%s "\$LOCAL_TARGET_PARQUET") -le 12 ]; then
        printf "%s [WARNING] %s is corrupted or empty (<12 bytes). Seeding valid schema skeleton frame...\\n" \\
            "\$(date '+%Y-%m-%d %H:%M:%S')" "\$LOCAL_TARGET_PARQUET" >> "\$TXT_LOG"
        
        ${env_exe} -c "import polars as pl; pl.DataFrame(schema={'condition': pl.String, 'epoch_id': pl.Int64}).write_parquet('\$LOCAL_TARGET_PARQUET')"
        _sync_to_parquet
    fi

    # --- Correction Override ---
    CORRECTION_DIR="\$CONTEXT_DIR/corrections/${scriptName}"
    HAS_CORRECTIONS=false
    if [ -d "\$CORRECTION_DIR" ]; then
        shopt -s nullglob
        _CORR_FILES=("\$CORRECTION_DIR"/*.parquet)
        shopt -u nullglob
        if [ \${#_CORR_FILES[@]} -gt 0 ]; then
            HAS_CORRECTIONS=true
            for _cf in "\${_CORR_FILES[@]}"; do cp "\$_cf" .; done
            printf "%s [CORRECTION] Applied \${#_CORR_FILES[@]} override(s), skipping script\\n" >> "\$TXT_LOG"
            _sync_to_parquet
        fi
    fi

    if [ "\$HAS_CORRECTIONS" != "true" ]; then
        TEMP_OUT=\$(mktemp)
        export VIS_LABEL_MAP='${params.vis_label_map}'
        
        ${env_exe} -u "${workflow.launchDir}/${script}" "\$LOCAL_TARGET_PARQUET" ${extraArgsString} 2>&1 | tee "\$TEMP_OUT"
        EXIT_CODE=\${PIPESTATUS}

        while IFS= read -r line; do
            printf "%s %s\\n" "\$(date '+%Y-%m-%d %H:%M:%S')" "\$line"
        done < "\$TEMP_OUT" >> "\$TXT_LOG"
        rm -f "\$TEMP_OUT"
        _sync_to_parquet

        if [ \$EXIT_CODE -ne 0 ]; then
            IS_EMPTY_SKELETON=\$( ${env_exe} -c "import polars as pl; print(pl.read_parquet('\$LOCAL_TARGET_PARQUET').height == 0)" 2>/dev/null || echo "false" )
            if [ "\$IS_EMPTY_SKELETON" = "True" ]; then
                printf "%s [SUCCESS] Input skeleton was empty. Safely bypassed execution exception.\\n" "\$(date '+%Y-%m-%d %H:%M:%S')" >> "\$TXT_LOG"
                EXIT_CODE=0
            else
                printf "\\n%s [ERROR] ${scriptName} exit code %d\\n" "\$(date '+%Y-%m-%d %H:%M:%S')" \$EXIT_CODE >> "\$TXT_LOG"
                _sync_to_parquet
                exit \$EXIT_CODE
            fi
        fi
    fi

    # --- Secure Bash Interception ---
    FINAL_OUTPUT="${resolvedBaseName}.parquet"
    GENERATED_SCRIPT_FILE=\$(ls *.parquet | grep -v "\$LOCAL_TARGET_PARQUET" | head -n 1 || echo "")
    
    if [ -n "\$GENERATED_SCRIPT_FILE" ] && [ -f "\$GENERATED_SCRIPT_FILE" ]; then
        mv "\$GENERATED_SCRIPT_FILE" "\$FINAL_OUTPUT"
    else
        ${env_exe} -c "import polars as pl; pl.DataFrame(schema={'condition': pl.String, 'epoch_id': pl.Int64}).write_parquet('\$FINAL_OUTPUT')"
    fi

    printf "%s [INFO] Module execution finalized. Normalized output asset: %s\\n" "\$(date '+%Y-%m-%d %H:%M:%S')" "\$FINAL_OUTPUT" >> "\$TXT_LOG"
    _sync_to_parquet
    exit 0
    """
}