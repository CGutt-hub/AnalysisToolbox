process FINALIZE_L1 {

    publishDir (
        path: { "${params.output_dir}/${params.project_name}_l1/${participant_id}" },
        mode: 'copy',
        pattern: "*.parquet"
    )

    input:
        tuple val(participant_id), val(participant_bin), path(incoming_signals)

    output:
        tuple val(participant_id), path("*.parquet")

    script:
        def contextDirPath = "${workflow.launchDir}/${params.output_dir}/${params.project_name}_l1/${participant_id}/.bin"
        def textLogPath    = "${contextDirPath}/${participant_id}.log"
        def outputName     = "${participant_id}_final.parquet"

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
            printf "%s [L1] [%s] %s\\n" "\$(date '+%Y-%m-%d %H:%M:%S')" "\$level_lvl" "\$message" >> "\$TXT_LOG"
        ) 200>>"\$LOCK_FILE"
    }

    TARGET_FILE="${outputName}"

    log_msg "INFO" "FINALIZE_L1 - Aggregating modules for participant ${participant_id}..."

    # Execute Polars Concatenation with strict fail-fast error checking
    ${params.python_exe} -c "
import polars as pl, os, sys, glob

valid_files = [f for f in glob.glob('*.parquet') if f != '${outputName}' and os.path.getsize(f) > 12]

if not valid_files:
    print(f'[Finalize L1] ERROR: Zero valid incoming signal files found for ${participant_id}.')
    sys.exit(1)

dfs = []
for f in valid_files:
    try:
        dfs.append(pl.read_parquet(f))
    except Exception as e:
        print(f'[Finalize L1] ERROR: Could not read parquet asset {f}: {e}')
        sys.exit(1)

if not dfs:
    print(f'[Finalize L1] ERROR: No valid data frames populated for ${participant_id}.')
    sys.exit(1)

try:
    final_df = pl.concat(dfs, how='diagonal_relaxed')
    if final_df.height == 0:
        print(f'[Finalize L1] ERROR: Final combined dataframe for ${participant_id} is empty.')
        sys.exit(1)
    final_df.write_parquet('\$TARGET_FILE')
except Exception as e:
    print(f'[Finalize L1] ERROR: Polars concatenation failed for ${participant_id}: {e}')
    sys.exit(1)
" 2>&1 | tee -a temp_finalize.log

    EXIT_CODE=\${PIPESTATUS[0]}

    if [ \$EXIT_CODE -ne 0 ]; then
        LOG_ERR=\$(cat temp_finalize.log 2>/dev/null || echo "Unknown aggregation error")
        log_msg "ERROR" "FINALIZE_L1 failed for ${participant_id}: \$LOG_ERR"
        rm -f temp_finalize.log
        exit 1
    fi

    rm -f temp_finalize.log

    if [ ! -f "\$TARGET_FILE" ] || [ \$(stat -c %s "\$TARGET_FILE" 2>/dev/null || stat -f %z "\$TARGET_FILE" 2>/dev/null || echo 0) -le 12 ]; then
        log_msg "ERROR" "FINALIZE_L1 completed but output asset \$TARGET_FILE is missing or corrupt."
        exit 1
    fi

    log_msg "INFO" "FINALIZE_L1 complete. Output written to \$TARGET_FILE"
    """
}