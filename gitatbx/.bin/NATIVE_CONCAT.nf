// =========================================================================
// TRULY NATIVE CONCAT PROCESS (Singular Standalone Process Block - SIMPLE APPEND)
// =========================================================================
process NATIVE_CONCAT {
    tag "Cohort_Aggregation_${output_name}"
    
    publishDir (
        path: { "${params.output_dir}/${params.l2_folder ?: 'l2_analysis'}" },
        mode: 'copy',
        pattern: "*.parquet"
    )

    input:
        // 💡 Nextflow natively collects the queue stream here, flattens it, 
        // and drops a clean, iterable array of Files into 'cohort_files'
        path cohort_files
        val output_name          

    output:
        tuple val("group"), path("*.parquet"), emit: cohort_matrix

    // === PURE AGNOSTIC BASH SUB-SHELL (0% JVM Evaluation Overhead) ===
    script:
        // Filter out any potential empty or corrupted files safely
        def cleanFiles = cohort_files.findAll { f -> 
            f != null && f.toString().endsWith('.parquet') && new java.io.File(f.toString()).exists() && new java.io.File(f.toString()).length() > 12 
        }
        def fileArgumentsStr = cleanFiles.collect { f -> "'${f.toString()}'" }.join(' ')

    """
    #!/bin/bash
    set -e

    # 💡 CANONICAL NAMING SIMPLIFIED: Simply append _concat to the clean output_name!
    TARGET_FILE="${output_name}_concat.parquet"

    if [ -z "${fileArgumentsStr}" ]; then
        echo "[Native Concat] WARNING: Zero active cohort data vectors detected. Seeding empty matrix..."
        ${params.python_exe} -c "import polars as pl; pl.DataFrame(schema={'condition': pl.String, 'epoch_id': pl.Int64}).write_parquet('\$TARGET_FILE')"
        exit 0
    fi

    # Low-level binary appending combines all participant data frames instantly
    cat ${fileArgumentsStr} > "\$TARGET_FILE"
    
    echo "[Native Concat] Successfully created base-appended matrix: \$TARGET_FILE"
    """
}