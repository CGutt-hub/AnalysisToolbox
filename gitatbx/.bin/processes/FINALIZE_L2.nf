process FINALIZE_L2 {

    publishDir (
        path: { "${params.output_dir}/${params.l2_folder ?: params.project_name + '_l2'}" },
        mode: 'copy',
        pattern: "*.parquet"
    )

    input:
        path incoming_signals

    output:
        path "*.parquet"

    script:
        def l2FolderName   = params.l2_folder ?: "${params.project_name}_l2"
        def contextDirPath = "${workflow.launchDir}/${params.output_dir}/${l2FolderName}/.bin"
        def textLogPath    = "${contextDirPath}/${params.project_name}_l2.log"
        def outputName     = "cohort_summary_final.parquet"

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
            printf "%s [L2] [%s] %s\\n" "\$(date '+%Y-%m-%d %H:%M:%S')" "\$level_lvl" "\$message" >> "\$TXT_LOG"
        ) 200>>"\$LOCK_FILE"
    }

    TARGET_FILE="${outputName}"

    log_msg "INFO" "FINALIZE_L2 - Generating global cohort summary..."

    ${params.python_exe} -c "
import polars as pl, os, sys, glob

valid_files = [f for f in glob.glob('*.parquet') if f != '${outputName}' and os.path.getsize(f) > 12]

if not valid_files:
    print('[Finalize L2] ERROR: No valid L2 parquet assets provided for cohort summary.')
    sys.exit(1)

dfs = []
for f in valid_files:
    try:
        dfs.append(pl.read_parquet(f))
    except Exception as e:
        print(f'[Finalize L2] ERROR: Failed reading file {f}: {e}')
        sys.exit(1)

if not dfs:
    print('[Finalize L2] ERROR: Dataframe array empty during L2 summary generation.')
    sys.exit(1)

try:
    final_df = pl.concat(dfs, how='diagonal_relaxed')
    if final_df.height == 0:
        print('[Finalize L2] ERROR: Combined cohort summary dataframe has 0 rows.')
        sys.exit(1)
    final_df.write_parquet('\$TARGET_FILE')
except Exception as e:
    print(f'[Finalize L2] ERROR: Polars failure during cohort concat: {e}')
    sys.exit(1)
" 2>&1 | tee -a temp_finalize_l2.log

    EXIT_CODE=\${PIPESTATUS[0]}

    if [ \$EXIT_CODE -ne 0 ]; then
        LOG_ERR=\$(cat temp_finalize_l2.log 2>/dev/null || echo "Unknown L2 error")
        log_msg "ERROR" "FINALIZE_L2 failed: \$LOG_ERR"
        rm -f temp_finalize_l2.log
        exit 1
    fi

    rm -f temp_finalize_l2.log

    if [ ! -f "\$TARGET_FILE" ] || [ \$(stat -c %s "\$TARGET_FILE" 2>/dev/null || stat -f %z "\$TARGET_FILE" 2>/dev/null || echo 0) -le 12 ]; then
        log_msg "ERROR" "FINALIZE_L2 completed but \$TARGET_FILE was not written correctly."
        exit 1
    fi

    log_msg "INFO" "FINALIZE_L2 complete. Cohort package generated successfully: \$TARGET_FILE"
    """
}