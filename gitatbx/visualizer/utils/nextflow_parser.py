"""
Nextflow Pipeline Parser

Parses .nf files to extract:
- Module aliases (from include block)
- Channel definitions
- Process calls
- Connections between processes
"""

import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass

from ..models.pipeline_model import Pipeline, PipelineNode
from ..models.module_model import ModuleInfo, ModuleType


@dataclass
class ParseResult:
    """Result of parsing a Nextflow pipeline file."""
    pipeline: Optional[Pipeline] = None
    errors: List[str] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []


class NextflowParser:
    """Parser for Nextflow DSL2 pipeline files."""
    
    def __init__(self, atbx_path: str = None):
        self.atbx_path = Path(atbx_path) if atbx_path else None
        self.module_registry = None
    
    def parse_file(self, file_path: str) -> ParseResult:
        """Parse a Nextflow pipeline file."""
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            return self.parse_content(content, file_path)
        except Exception as e:
            return ParseResult(
                pipeline=None,
                errors=[f"Failed to read file: {str(e)}"]
            )
    
    def parse_content(self, content: str, file_path: str = "") -> ParseResult:
        """Parse Nextflow pipeline content."""
        errors = []
        warnings = []
        
        # Extract include block
        include_block, include_errors = self._extract_include_block(content)
        errors.extend(include_errors)
        
        # Extract workflow block
        workflow_content, workflow_errors = self._extract_workflow_block(content)
        errors.extend(workflow_errors)
        
        if errors:
            return ParseResult(pipeline=None, errors=errors, warnings=warnings)
        
        # Parse include block for module aliases
        module_aliases = self._parse_module_aliases(include_block)
        
        # Create pipeline
        pipeline_name = self._extract_pipeline_name(content, file_path)
        pipeline = Pipeline(
            pipeline_id=str(hash(file_path + content)),
            name=pipeline_name,
            pipeline_file=file_path
        )
        
        # Parse workflow for process calls
        process_calls = self._parse_process_calls(workflow_content)
        
        # Parse channel connections
        connections = self._parse_connections(workflow_content)
        
        # Build pipeline nodes from process calls
        for call in process_calls:
            node = self._create_node_from_call(call)
            if node:
                pipeline.nodes[node.node_id] = node
        
        # Add connections
        for conn in connections:
            pipeline.connections[conn.connection_id] = conn
        
        return ParseResult(pipeline=pipeline, errors=errors, warnings=warnings)
    
    def _extract_include_block(self, content: str) -> Tuple[str, List[str]]:
        """Extract the include block containing module aliases."""
        errors = []
        pattern = r'include\s*\{(.*?)\}\s*from\s*[\'"](.*?)[\'"]'
        matches = re.findall(pattern, content, re.DOTALL)
        
        if not matches:
            errors.append("No include block found in pipeline file")
            return "", errors
        
        # Return the first include block (most pipelines have one)
        return matches[0][0], errors
    
    def _extract_workflow_block(self, content: str) -> Tuple[str, List[str]]:
        """Extract the workflow block."""
        errors = []
        pattern = r'workflow\s*\{(.*?)\}'
        matches = re.findall(pattern, content, re.DOTALL)
        
        if not matches:
            errors.append("No workflow block found in pipeline file")
            return "", errors
        
        return matches[0], errors
    
    def _extract_pipeline_name(self, content: str, file_path: str) -> str:
        """Extract pipeline name from content or file path."""
        # Try to find a comment with pipeline name
        match = re.search(r'//\s*([^\n]+)\s*Pipeline', content)
        if match:
            return match.group(1).strip()
        
        # Use file name
        if file_path:
            return Path(file_path).stem.replace('_pipeline', '').replace('.nf', '')
        
        return "Unnamed Pipeline"
    
    def _parse_module_aliases(self, include_content: str) -> List[str]:
        """Parse module aliases from include block."""
        # Remove everything after 'from' to get just the alias list
        alias_block = include_content.split('from')[0]
        
        # Split by semicolon and clean up
        aliases = []
        for part in alias_block.split(';'):
            part = part.strip()
            if part and not part.startswith('//'):
                # Remove inline comments
                part = part.split('//')[0].strip()
                if part:
                    aliases.append(part)
        
        return aliases
    
    def _parse_process_calls(self, workflow_content: str) -> List[Dict]:
        """Parse process calls in workflow block."""
        calls = []
        
        # Pattern: process_name(params, params, ...)
        # This regex captures: (name) ( (args) )
        pattern = r'(\w+)\s*\((.*?)\)'
        
        for match in re.finditer(pattern, workflow_content):
            name = match.group(1)
            args_str = match.group(2)
            
            # Parse arguments
            args = self._parse_arguments(args_str)
            
            calls.append({
                'name': name,
                'args': args,
                'raw': match.group(0)
            })
        
        return calls
    
    def _parse_arguments(self, args_str: str) -> List[str]:
        """Parse comma-separated arguments.
        
        Handles nested parentheses and quoted strings.
        """
        args = []
        current = ""
        depth = 0
        in_quote = False
        quote_char = None
        
        for char in args_str:
            if char in ('"', "'") and (not in_quote or char == quote_char):
                in_quote = not in_quote
                if in_quote:
                    quote_char = char
                    current += char
                else:
                    quote_char = None
                    current += char
            elif char == '(' and not in_quote:
                depth += 1
                current += char
            elif char == ')' and not in_quote:
                depth -= 1
                current += char
            elif char == ',' and depth == 0 and not in_quote:
                args.append(current.strip())
                current = ""
            else:
                current += char
        
        if current.strip():
            args.append(current.strip())
        
        return args
    
    def _parse_connections(self, workflow_content: str) -> List[Dict]:
        """Parse connections between channels."""
        connections = []
        
        # Look for patterns like: a.mix(b) or Channel.mix([a, b])
        # For now, return empty - connection parsing is complex
        # and would need full AST parsing of Nextflow DSL
        
        return connections
    
    def _create_node_from_call(self, call: Dict) -> Optional[PipelineNode]:
        """Create a pipeline node from a process call."""
        name = call['name']
        args = call['args']
        
        # This is a simplified approach
        # In reality, we'd need to match the arguments to the IOInterface signature
        # (env_exe, script, input, extraParams)
        
        # For now, create a placeholder node
        module_info = ModuleInfo(
            id=name,
            name=name.replace('_', ' ').title(),
            module_type=ModuleType.PROCESSOR,  # Default
            category="",
            description=f"Module: {name}",
            script_path=f"{name}.py",
            inputs=[IOChannel(name="input", type="path")],
            outputs=[IOChannel(name="output", type="path")]
        )
        
        node_id = f"{name}_node"
        node = PipelineNode(
            node_id=node_id,
            module_info=module_info,
            position=(0, 0)
        )
        
        return node
