process NATIVE_SPLIT {
    executor 'local'
    
    input:
        tuple val(id), path(context_path)
        val full_pattern

    output:
        tuple val(id), path("*.{fif,parquet}"), emit: isolated_file

    script:
    """
    #!/bin/bash
    set -e
    
    if [ -f "${context_path}" ]; then
        SRC_FILE="${context_path}"
    else
        SRC_FILE=\$(find "${context_path}" -maxdepth 1 -name "${full_pattern}" | head -n 1)
    fi

    if [ -z "\${SRC_FILE}" ]; then
        echo "[Split Engine] ERROR: Keine passende Datei für Muster '${full_pattern}' in ${context_path} gefunden!"
        exit 1
    fi

    BASE_STEM=\$(basename "\${SRC_FILE}" | sed 's/\\.[^.]*\$//')
    EXTENSION=\${SRC_FILE##*.}
    
    OUT_NAME="\${BASE_STEM}.\${EXTENSION}"
    
    if [ "\$(basename "\${SRC_FILE}")" = "\${OUT_NAME}" ]; then
        cp -P "\${SRC_FILE}" "isolated_\${OUT_NAME}"
    else
        cp -P "\${SRC_FILE}" "\${OUT_NAME}"
    fi
    """
}