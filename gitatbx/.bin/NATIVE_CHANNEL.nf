// =========================================================================
// NATIVE CHANNEL PROCESS (Generischer Staging-Baustein - NATIVE SCRIPT)
// =========================================================================
process NATIVE_CHANNEL {
    tag "Stage_${id}_${pattern_label}"
    executor 'local'
    
    input:
        // _discovered_l1_folder sollte idealerweise ein reiner String-Pfad sein
        tuple val(id), val(_discovered_l1_folder)
        val file_pattern        
        val pattern_label       

    output:
        // WICHTIG: Keine feste Dateiendung! Er greift alles, was gestaged wurde.
        tuple val(id), path("staged_${pattern_label}_${id}.*"), emit: staged_channel

    script:
    """
    #!/bin/bash
    set -e # Bricht das Skript bei echten Fehlern sofort ab

    # 1. AGNOSTISCHE PFAD-AUFLÖSUNG (Cross-Sandbox absolute resolution)
    if [ -n "${_discovered_l1_folder}" ]; then
        if [[ "${_discovered_l1_folder}" = /* ]]; then
            INPUT_PATH="${_discovered_l1_folder}"
        else
            INPUT_PATH="${workflow.launchDir}/${_discovered_l1_folder}"
        fi
    fi

    # 2. FEHLERTOLERANTE ID-EXTRAKTION (Handles strings like EV2_019, Subj_1, Part01)
    # Extrahiert den allerletzten zusammenhängenden Ziffernblock
    NUM=\$(echo "${id}" | grep -oE '[0-9]+\$' | sed 's/^0*//' || echo "")
    if [ -n "\$NUM" ]; then
        # Präfix ist alles vor dem spezifischen numerischen Endblock
        PREFIX=\$(echo "${id}" | sed -E 's/[0-9]+\$//')
    else
        # Fallback für komplett nicht-numerische IDs (z.B. "ControlGroup")
        PREFIX="${id}"
    fi

    # 3. ZWEI-WEGE VERZEICHNIS-SCAN (Resolves the Over-Cross Path Dilemma)
    TARGET_DIR=""
    
    # Pfad-Weiche A: Scan innerhalb des übergebenen Kontext-Verzeichnisses
    if [ -n "\$INPUT_PATH" ] && [ -d "\$INPUT_PATH" ]; then
        if [ -z "\$NUM" ]; then
            TARGET_DIR=\$(find "\$INPUT_PATH" -maxdepth 1 -type d -name "${id}" | head -n 1)
        else
            # Wir nutzen eine native Shell-Expansion-Schleife, um Groovy-Brackets-Kollisionen zu vermeiden
            for PATTERN in "\${PREFIX}\${NUM}" "\${PREFIX}0\${NUM}" "\${PREFIX}00\${NUM}"; do
                FOUND=\$(find "\$INPUT_PATH" -maxdepth 1 -type d -name "\$PATTERN" | head -n 1)
                if [ -n "\$FOUND" ]; then TARGET_DIR="\$FOUND"; break; fi
            done
        fi
    fi

    # Pfad-Weiche B: Scan im globalen Input-Repository (Falls A leer oder ungültig war)
    if [ -z "\$TARGET_DIR" ] || [ ! -d "\$TARGET_DIR" ]; then
        if [ -d "${workflow.launchDir}/${params.input_dir}" ]; then
            GLOBAL_BASE="${workflow.launchDir}/${params.input_dir}"
        else
            GLOBAL_BASE="${workflow.projectDir}/${params.input_dir}"
        fi
        
        if [ -d "\$GLOBAL_BASE" ]; then
            if [ -z "\$NUM" ]; then
                TARGET_DIR=\$(find "\$GLOBAL_BASE" -maxdepth 1 -type d -name "${id}" | head -n 1)
            else
                for PATTERN in "\${PREFIX}\${NUM}" "\${PREFIX}0\${NUM}" "\${PREFIX}00\${NUM}"; do
                    FOUND=\$(find "\$GLOBAL_BASE" -maxdepth 1 -type d -name "\$PATTERN" | head -n 1)
                    if [ -n "\$FOUND" ]; then TARGET_DIR="\$FOUND"; break; fi
                done
            fi
        fi
    fi

    # 4. UNIVERSELLE DATEI-RECHERCHE MIT WILD-CARD SANITIZATION
    SRC_FILE=""
    if [ -n "\$TARGET_DIR" ] && [ -d "\$TARGET_DIR" ]; then
        # Bereinigt Maskierungszeichen und Anführungszeichen für ein sauberes case-insensitive Match
        CLEAN_PATTERN=\$(echo "${file_pattern}" | sed 's/[\\*\\"]//g')
        SRC_FILE=\$(find "\$TARGET_DIR" -maxdepth 1 -type f -iname "*\${CLEAN_PATTERN}*" | head -n 1)
    fi

    # 5. STRUKTURELLE FLUSS-SICHERUNG (Prevents Kryo ReferenceQueue Serialization errors)
    # Erzeugt im Fehlerfall ein leeres Token, um das asynchrone Stream-Vakuum zu verhindern
    if [ -z "\$SRC_FILE" ]; then
        echo "[Native Channel] WARNING: Agnostic search returned empty for Pattern '${file_pattern}' and ID '${id}'."
        echo "Polymorphic fallback triggered to preserve downstream Dataflow Streams."
        
        # Bestimmt die Dateiendung dynamisch aus dem Suchmuster, Fallback auf parquet
        DETECTED_EXT=\$(echo "${file_pattern}" | grep -oE '\\.[a-zA-Z0-9]+\$' | sed 's/\\.//' || echo "parquet")
        OUTPUT_FILE="staged_${pattern_label}_${id}.\${DETECTED_EXT}"
        
        touch "\$OUTPUT_FILE"
        echo "[Native Channel] Empty placeholder context serialized: \$OUTPUT_FILE"
        exit 0
    fi

    # 6. STANDARDISIERTES STAGING VIA SYMLINK
    EXTENSION="\${SRC_FILE##*.}"
    ln -s "\$SRC_FILE" "staged_${pattern_label}_${id}.\${EXTENSION}"
    echo "[Native Channel] Agnostic staging completed successfully for ${id} -> \$SRC_FILE"
    """
}