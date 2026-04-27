#!/usr/bin/env nextflow
nextflow.enable.dsl=2

// =========================================
// Nextflow Pipeline Structural Patterns
// =========================================
// This template demonstrates the core Nextflow syntactical patterns used for chaining processes in data analysis pipelines.
// It focuses on channel operations, participant management, and process orchestration - not specific analysis content.
// Use this as a reference for building robust, scalable pipelines.

// =========================================
// MODULE INCLUSION PATTERNS
// =========================================
// Include all processes from your project modules file
// Edit {name}_modules.nf to add/remove processes
include { participant_discovery; finalize_participant; finalize_l2; your_process1; your_process2 } from './__MODULES_FILE__'

// =========================================
// TERMINAL PROCESS DEFINITION
// =========================================
// Define processes that indicate pipeline completion (typically final plotters)
// Used by workflow_wrapper for finalization triggering
def terminal_process_names = ['your_final_process1', 'your_final_process2']

// =========================================
// WORKFLOW DEFINITION WITH CHANNEL PATTERNS
// =========================================
workflow {

    // =========================================
    // PATTERN 1: PARTICIPANT DISCOVERY AND CONTEXT MANAGEMENT
    // =========================================
    // workflow_wrapper discovers participants, creates output folders, manages finalization
    // Input: input_dir, output_dir, participant_pattern, terminal_process_names
    // Output: participant_context ([pid, folder]), participant_finalized (trigger after completion)
    workflow_wrapper(params.input_dir, params.output_dir, params.participant_pattern, terminal_process_names)

    // Extract participant channels
    participant_context = workflow_wrapper.out.participant_context  // Channel of [participant_id, output_folder_path]
    participant_id = participant_context.map { it[0] }  // Channel of participant IDs only

    // =========================================
    // PATTERN 2: DATA INGESTION WITH PARTICIPANT MAPPING
    // =========================================
    // Map participant IDs to file paths for reading
    // Pattern: participant_id.map { id -> "path/to/${id}/file" }
    input_files = participant_id.map { id ->
        "${workflow.launchDir}/${params.input_dir}/${id}/${id}.extension"
    }

    // =========================================
    // PATTERN 3: PROCESS CALLING PATTERNS (IOInterface)
    // =========================================
    // All processes follow the IOInterface pattern for consistent execution
    // Pattern: your_process(params.python_exe, params.script_path, input_channel, "extra_args_string")

    // Data reading: Load raw data from input files
    raw_data = your_data_reader(params.python_exe, params.reader_script, input_files, "reader_params")

    // File extraction: Extract specific outputs from multi-output processes
    stream1 = your_file_finder(params.python_exe, params.finder_script, raw_data, "stream1_pattern")
    stream2 = your_file_finder(params.python_exe, params.finder_script, raw_data, "stream2_pattern")

    // Multi-input processing: Combine multiple inputs per participant
    multi_input = participant_id
        .join(stream1.map { f -> [extract_pid(f), f] })
        .join(stream2.map { f -> [extract_pid(f), f] })
        .map { pid, f1, f2 -> [f1, f2] }

    combined_output = your_multi_input_process(params.python_exe, params.process_script, multi_input, "params")

    // =========================================
    // PATTERN 4: JOINING CHANNELS BY PARTICIPANT ID
    // =========================================
    // Essential for maintaining participant-specific data relationships
    // Pattern: channel.map { f -> [extract_pid(f), f] }.join(other_channel)

    // Aggregation joining: Collect multiple outputs per participant
    all_conditions = participant_id
        .join(stream1.map { f -> [extract_pid(f), f] })
        .join(stream2.map { f -> [extract_pid(f), f] })
        .map { pid, f1, f2 -> [f1, f2] }

    // Context joining: Join results with participant context for output paths
    final_with_context = all_conditions
        .map { pid, f1, f2 -> [pid, [f1, f2]] }
        .join(participant_context)

    // Extract components for final output generation
    final_data = final_with_context.map { pid, files, folder -> files }
    output_paths = final_with_context.map { pid, files, folder ->
        "${workflow.launchDir}/${folder} ${pid}_results"
    }

    // =========================================
    // FINALIZATION PATTERNS (handled by workflow_wrapper)
    // =========================================
    // - Monitors terminal processes for completion (COMPLETED, FAILED, CACHED)
    // - Triggers when all terminal processes have status
    // - Logs completion, commits to git with "autosync results: {pid} {status}"
    // - Pushes if remote configured
    // - participant_finalized channel emits after finalization
}