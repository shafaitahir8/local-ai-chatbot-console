"""
Template Service - Manages bot templates.
"""

import os
import json
from typing import Dict, Any, List, Optional
from pathlib import Path


class TemplateService:
    """
    Service for managing chatbot templates.
    """
    
    def __init__(self, templates_dir: str = None):
        self.templates_dir = templates_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'templates'
        )
        os.makedirs(self.templates_dir, exist_ok=True)
        
        # Create default templates if they don't exist
        self._ensure_default_templates()
    
    def _ensure_default_templates(self):
        """Create default templates if they don't exist."""
        templates = {
            'openai_template.json': {
                "name": "OpenAI GPT",
                "provider": "openai",
                "description": "Standard OpenAI GPT model template",
                "required_fields": ["api_key"],
                "optional_fields": ["model", "temperature", "max_tokens"],
                "default_model": "gpt-4.1-mini",
                "default_temperature": 0.7,
                "default_max_tokens": 2000,
                "default_system_prompt": "You are a helpful AI assistant. Answer questions accurately and concisely.",
                "supports_memory": True,
                "supports_temperature": True,
                "supports_streaming": True,
                "available_models": [
                    "gpt-4.1",
                    "gpt-4.1-mini",
                    "gpt-4.1-nano",
                    "gpt-4o",
                    "gpt-4o-mini",
                    "gpt-4-turbo",
                    "gpt-3.5-turbo"
                ]
            },
            'local_template.json': {
                "name": "Local LLM",
                "provider": "local",
                "description": "Local LLM via llama.cpp, Ollama, or compatible server",
                "required_fields": [],
                "optional_fields": ["base_url", "model", "temperature", "max_tokens"],
                "default_model": "local",
                "default_temperature": 0.7,
                "default_max_tokens": 2000,
                "default_base_url": "http://localhost:8080",
                "default_system_prompt": "You are a helpful AI assistant running locally. Answer questions accurately and concisely.",
                "supports_memory": True,
                "supports_temperature": True,
                "supports_streaming": True,
                "available_models": [
                    "local",
                    "llama-3.3-70b",
                    "llama-3.2-3b",
                    "llama-3.1-8b",
                    "mistral-7b",
                    "mixtral-8x7b",
                    "phi-3",
                    "qwen-2.5"
                ]
            },
            'claude_template.json': {
                "name": "Anthropic Claude",
                "provider": "claude",
                "description": "Anthropic Claude model template",
                "required_fields": ["api_key"],
                "optional_fields": ["model", "temperature", "max_tokens"],
                "default_model": "claude-sonnet-4-20250514",
                "default_temperature": 0.7,
                "default_max_tokens": 2000,
                "default_system_prompt": "You are Claude, a helpful AI assistant created by Anthropic. Answer questions thoughtfully and accurately.",
                "supports_memory": True,
                "supports_temperature": True,
                "supports_streaming": True,
                "available_models": [
                    "claude-sonnet-4-20250514",
                    "claude-opus-4-20250514",
                    "claude-3-7-sonnet-20250219",
                    "claude-3-5-sonnet-20241022",
                    "claude-3-5-haiku-20241022",
                    "claude-3-opus-20240229"
                ]
            },
            'gemini_template.json': {
                "name": "Google Gemini (Free Tier)",
                "provider": "gemini",
                "description": "Google Gemini API template optimized for low-cost/free-tier usage",
                "required_fields": ["api_key"],
                "optional_fields": ["model", "temperature", "max_tokens"],
                "default_model": "gemini-2.5-flash",
                "default_temperature": 0.6,
                "default_max_tokens": 1200,
                "default_system_prompt": "You are a helpful AI assistant. Keep answers concise and accurate.",
                "supports_memory": True,
                "supports_temperature": True,
                "supports_streaming": False,
                "available_models": [
                    "gemini-2.5-flash",
                    "gemini-2.0-flash-lite",
                    "gemini-2.0-flash",
                    "gemini-1.5-flash"
                ]
            },
            'customer_support_template.json': {
                "name": "Customer Support Bot",
                "provider": "openai",
                "description": "Pre-configured template for customer support chatbots",
                "required_fields": ["api_key"],
                "optional_fields": ["model", "temperature", "max_tokens", "company_name"],
                "default_model": "gpt-4.1-mini",
                "default_temperature": 0.5,
                "default_max_tokens": 1500,
                "default_system_prompt": """You are a helpful customer support assistant for {company_name}. Your role is to:
1. Answer customer questions accurately and professionally
2. Help resolve issues and concerns
3. Provide information about products and services
4. Escalate complex issues when necessary

Always be polite, patient, and helpful. If you don't know something, say so and offer to connect the customer with a human agent.""",
                "supports_memory": True,
                "supports_temperature": True,
                "supports_streaming": True,
                "available_models": [
                    "gpt-4.1-mini",
                    "gpt-4.1",
                    "gpt-4o-mini"
                ]
            },
            'knowledge_base_template.json': {
                "name": "Knowledge Base Bot",
                "provider": "openai",
                "description": "Template optimized for RAG-based knowledge retrieval",
                "required_fields": ["api_key"],
                "optional_fields": ["model", "temperature", "max_tokens"],
                "default_model": "gpt-4.1-mini",
                "default_temperature": 0.3,
                "default_max_tokens": 2000,
                "default_system_prompt": """You are a knowledgeable assistant that answers questions based on the provided context.

IMPORTANT RULES:
1. Only answer based on the provided context
2. If the context doesn't contain the answer, say "I don't have that information in my knowledge base"
3. Always be accurate and cite your sources when possible
4. Keep answers clear and well-structured

Context will be provided with each question.""",
                "supports_memory": True,
                "supports_temperature": True,
                "supports_streaming": True,
                "available_models": [
                    "gpt-4.1-mini",
                    "gpt-4.1",
                    "gpt-4o-mini"
                ]
            }
        }
        
        for filename, template in templates.items():
            filepath = os.path.join(self.templates_dir, filename)
            if not os.path.exists(filepath):
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(template, f, indent=2)
    
    def list_templates(self) -> List[Dict[str, Any]]:
        """
        List all available templates.
        
        Returns:
            List of template information dictionaries
        """
        templates = []
        
        for filename in os.listdir(self.templates_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(self.templates_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        template = json.load(f)
                        template['filename'] = filename
                        template['template_id'] = filename.replace('.json', '')
                        templates.append(template)
                except Exception as e:
                    print(f"Error loading template {filename}: {e}")
        
        return templates
    
    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific template by ID.
        
        Args:
            template_id: Template identifier (filename without .json)
            
        Returns:
            Template dictionary or None
        """
        filename = f"{template_id}.json" if not template_id.endswith('.json') else template_id
        filepath = os.path.join(self.templates_dir, filename)
        
        if not os.path.exists(filepath):
            return None
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                template = json.load(f)
                template['template_id'] = template_id.replace('.json', '')
                return template
        except Exception:
            return None
    
    def create_template(
        self,
        template_id: str,
        template_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a new template.
        
        Args:
            template_id: Template identifier
            template_data: Template configuration
            
        Returns:
            Created template
        """
        filename = f"{template_id}.json" if not template_id.endswith('.json') else template_id
        filepath = os.path.join(self.templates_dir, filename)
        
        if os.path.exists(filepath):
            raise ValueError(f"Template '{template_id}' already exists")
        
        # Validate required fields
        required = ['name', 'provider']
        for field in required:
            if field not in template_data:
                raise ValueError(f"Missing required field: {field}")
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(template_data, f, indent=2)
        
        template_data['template_id'] = template_id.replace('.json', '')
        return template_data
    
    def update_template(
        self,
        template_id: str,
        template_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update an existing template.
        
        Args:
            template_id: Template identifier
            template_data: Updated configuration
            
        Returns:
            Updated template
        """
        filename = f"{template_id}.json" if not template_id.endswith('.json') else template_id
        filepath = os.path.join(self.templates_dir, filename)
        
        if not os.path.exists(filepath):
            raise ValueError(f"Template '{template_id}' not found")
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(template_data, f, indent=2)
        
        template_data['template_id'] = template_id.replace('.json', '')
        return template_data
    
    def delete_template(self, template_id: str) -> bool:
        """
        Delete a template.
        
        Args:
            template_id: Template identifier
            
        Returns:
            True if deleted
        """
        filename = f"{template_id}.json" if not template_id.endswith('.json') else template_id
        filepath = os.path.join(self.templates_dir, filename)
        
        if not os.path.exists(filepath):
            return False
        
        os.remove(filepath)
        return True
    
    def get_default_config_for_template(
        self,
        template_id: str
    ) -> Dict[str, Any]:
        """
        Get default configuration values for a template.
        
        Args:
            template_id: Template identifier
            
        Returns:
            Default configuration dictionary
        """
        template = self.get_template(template_id)
        if not template:
            return {}
        
        return {
            'provider_type': template.get('provider', 'openai'),
            'template_type': template_id.replace('.json', ''),
            'model': template.get('default_model'),
            'temperature': template.get('default_temperature', 0.7),
            'max_tokens': template.get('default_max_tokens', 2000),
            'system_prompt': template.get('default_system_prompt', ''),
            'memory_enabled': template.get('supports_memory', True),
            'analytics_enabled': True
        }


# Singleton instance
template_service = TemplateService()
