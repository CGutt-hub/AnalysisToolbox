process NATIVE_CHANNEL {
    executor 'local'
    
    input:
        tuple val(id), val(_discovered_l1_folder)
        val file_pattern        
        val plot_type       

    output:
        tuple val(id), path("${id}_*.parquet"), emit: staged_channel

    script:
    def l1_folder = _discovered_l1_folder ?: ""
    def env_exe = params.containsKey('env_exe') ? params.env_exe : 'python3'

    """
    #!/bin/bash
    set -e

    CURRENT_ID="${id}"
    L1_FOLDER="${l1_folder}"
    PLOT_TYPE="${plot_type}"
    LAUNCH_DIR="${workflow.launchDir}"
    PROJ_DIR="${workflow.projectDir}"
    INP_DIR="${params.input_dir}"
    PYTHON_EXE="${env_exe}"

    # 1. Standardize pattern: strip wildcards, leading underscores/dots, and extensions
    CLEAN_TAG=\$(echo "${file_pattern}" | sed -e 's/[*"]//g' -e 's/^\\._//' -e 's/^_//' -e 's/\\.parquet\$//i' -e 's/\\.fif\$//i')
    
    # Reconstruct strict extension target and declared tag
    FILE_PATTERN="*\${CLEAN_TAG}.parquet"
    DECLARED_TAG="\${CLEAN_TAG}"
    CANONICAL_NAME="\${CURRENT_ID}_\${DECLARED_TAG}.parquet"

    # 2. Resolve participant input directory
    PARTICIPANT_SRC_DIR=""
    for base in "\$LAUNCH_DIR/\$INP_DIR" "\$PROJ_DIR/\$INP_DIR" "\$INP_DIR"; do
        if [ -d "\$base/\$CURRENT_ID" ]; then
            PARTICIPANT_SRC_DIR="\$base/\$CURRENT_ID"
            break
        fi
    done

    if [ -z "\$PARTICIPANT_SRC_DIR" ]; then
        echo "[Native Channel] ERROR: Directory for '\$CURRENT_ID' missing."
        exit 1
    fi

    # 3. Search for source file matching clean tag
    SRC_FILE=\$(find "\$PARTICIPANT_SRC_DIR" -maxdepth 1 -type f -iname "*\${DECLARED_TAG}*.parquet" | head -n 1)

    if [ -z "\$SRC_FILE" ]; then
        SRC_FILE=\$(find "\$PARTICIPANT_SRC_DIR" -maxdepth 2 -type f -iname "*\${CURRENT_ID}*\${DECLARED_TAG}*.parquet" | head -n 1)
    fi

    if [ -z "\$SRC_FILE" ]; then
        echo "[Native Channel] ERROR: Channel pattern matching '\${DECLARED_TAG}' not found in '\$PARTICIPANT_SRC_DIR'."
        exit 1
    fi

    # 4. Stage locally and copy to .bin destination
    cp "\$SRC_FILE" "\$CANONICAL_NAME"

    if [ -n "\$L1_FOLDER" ]; then
        if [[ "\$L1_FOLDER" = /* ]]; then
            TARGET_BIN_DIR="\$L1_FOLDER/.bin"
        else
            TARGET_BIN_DIR="\$LAUNCH_DIR/\$L1_FOLDER/.bin"
        fi
    else
        TARGET_BIN_DIR="\$LAUNCH_DIR/${params.output_dir}/${params.project_name}_l1/\$CURRENT_ID/.bin"
    fi

    mkdir -p "\$TARGET_BIN_DIR"
    COMPENDIUM_FILE="\$TARGET_BIN_DIR/\$CANONICAL_NAME"
    cp "\$SRC_FILE" "\$COMPENDIUM_FILE"

    echo "[Native Channel] Successfully staged: \$SRC_FILE -> \$CANONICAL_NAME"
    """
}