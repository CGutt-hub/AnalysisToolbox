#!/usr/bin/env nextflow
nextflow.enable.dsl=2

// =========================================
// Module Includes
// =========================================
// All processes are imported from workflow_wrapper.nf in the AnalysisToolbox.
// The toolbox path is set via params.toolbox_dir in parameters.config.

// Core workflow management (required)
include { participant_discovery; finalize_participant; finalize_l2 } from '__TOOLBOX_DIR__/bin/workflow_wrapper.nf'

// ── Readers ───────────────────────────────────────────────────────────────────
// include { IOInterface as my_reader } from '__TOOLBOX_DIR__/bin/workflow_wrapper.nf'

// ── Processors ────────────────────────────────────────────────────────────────
// include { IOInterface as my_processor } from '__TOOLBOX_DIR__/bin/workflow_wrapper.nf'

// ── Analyzers ─────────────────────────────────────────────────────────────────
// include { IOInterface as my_analyzer } from '__TOOLBOX_DIR__/bin/workflow_wrapper.nf'
