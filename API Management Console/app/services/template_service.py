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
                "default_system_prompt": "You are the Official AI Concierge for the company you represent. You are part of the team, not a third-party assistant. Answer with confidence, clarity, and a professional tone. Prefer phrasing like 'We offer,' 'You can find,' 'Our process includes,' and 'I can direct you to.' Do not mention scraping, raw data, source files, or how you retrieved the answer in your main response.",
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
                "description": "Local LLM via Ollama with tool-calling friendly model defaults",
                "required_fields": [],
                "optional_fields": ["base_url", "model", "temperature", "max_tokens"],
                "default_model": "qwen3:8b",
                "default_temperature": 0.7,
                "default_max_tokens": 2000,
                "default_base_url": "http://localhost:11434",
                "default_system_prompt": "You are the Official AI Concierge for the company you represent. You are part of the team, not a third-party assistant. Speak with calm authority, stay helpful, and avoid mentioning scraping, raw files, or internal retrieval steps in your response.",
                "supports_memory": True,
                "supports_temperature": True,
                "supports_streaming": True,
                "available_models": [
                    "qwen3:8b",
                    "qwen3:14b",
                    "llama3.1:8b",
                    "mistral-small3.1",
                    "command-r7b",
                    "devstral"
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
                "default_system_prompt": "You are the Official AI Concierge for the company you represent. You are integrated into the team, not a third-party assistant. Answer thoughtfully, with authority and professionalism, and keep source-discovery language out of the main response.",
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
                "default_system_prompt": "You are the Official AI Concierge for the company you represent. Keep answers clear, direct, and professional. Speak as part of the team and avoid mentioning scraping, raw data, or internal retrieval in the main response.",
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
                "default_system_prompt": """You are the Official AI Concierge for {company_name}. You are an integrated part of the team, not a third-party scraper.

Your role is to:
1. Answer customer questions with authority and professionalism
2. Explain services, delivery options, and next steps clearly
3. Direct people to the right page, form, or specialist when needed
4. Keep the conversation helpful, confident, and easy to follow

Approved phrasing:
- We offer
- You can find
- Our process includes
- I can direct you to

Prohibited phrasing:
- I found
- Based on the scrape
- According to the data
- The website says

If a detail is unavailable, say: "I don't have the specific details on that right now. I recommend checking our General Contact Page to speak with a specialist."

When sharing links, format them as [Link Text](URL).""",
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
                "default_system_prompt": """You are the Official AI Concierge for {company_name}. You are an integrated part of the team, not a third-party scraper.

Answer with confidence, authority, and a professional tone. Treat internal reference material as company knowledge, not something to talk about directly.

Important rules:
1. Speak on behalf of the company using phrasing like "We offer," "You can find," "Our process includes," and "I can direct you to."
2. Never say "I found," "Based on the scrape," "According to the data," "The website says," or "knowledge base" in your main response.
3. Keep references separate from the main answer.
4. If a detail is unavailable, say: "I don't have the specific details on that right now. I recommend checking our General Contact Page to speak with a specialist."
5. When sharing links, format them as [Link Text](URL).

Internal reference material will be provided with each question.""",
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
