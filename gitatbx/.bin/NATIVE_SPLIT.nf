// =========================================================================
// SPLIT-ZWEIG (1-zu-N / Struktur-Isolierung / Agnostischer Datei-Finder)
// =========================================================================
process NATIVE_SPLIT {
    tag "${id}_split"
    executor 'local'
    
    input:
        // Ein sauberes Input-Tuple [id, path_to_folder_or_file]
        tuple val(id), path(context_path)
        // Das generische Suchmuster (z.B. "*_eeg.parquet" oder "*.fif")
        val full_pattern

    output:
        // Emittiert ein absolut linter-konformes Tuple für die Folge-Module
        tuple val(id), path("*.{fif,parquet}"), emit: isolated_file

    script:
    """
    #!/bin/bash
    
    # Check 1: Wenn context_path direkt eine physische Datei ist
    if [ -f "${context_path}" ]; then
        SRC_FILE="${context_path}"
    else
        # Check 2: Wenn es ein Ordner ist, nutzen wir das übergebene full_pattern generisch!
        # find sucht blind nach dem Muster, head -n 1 zieht die erste gefundene Datei.
        SRC_FILE=\$(find "${context_path}" -maxdepth 1 -name "${full_pattern}" | head -n 1)
    fi

    if [ -z "\${SRC_FILE}" ]; then
        echo "[Split Engine] ERROR: Keine passende Datei für Muster '${full_pattern}' in ${context_path} gefunden!"
        exit 1
    fi

    # Dateiendung und Basisnamen absolut manipulationssicher extrahieren
    BASE_STEM=\$(basename "\${SRC_FILE}" | sed 's/\\.[^.]*\$//')
    EXTENSION=\${SRC_FILE##*.}
    
    # Setzt den neuen, eindeutigen Namen zusammen (z.B. EV2_001_eeg.parquet)
    OUT_NAME="\${BASE_STEM}_\${EXTENSION}"
    
    # Falls der Name durch den Fund bereits identisch ist, kopieren wir direkt, 
    # andernfalls erzwingen wir die strukturierte Isolierung
    if [ "\$(basename "\${SRC_FILE}")" = "\${OUT_NAME}" ]; then
        cp -P "\${SRC_FILE}" "isolated_\${OUT_NAME}"
    else
        cp -P "\${SRC_FILE}" "\${OUT_NAME}"
    fi
    """
}