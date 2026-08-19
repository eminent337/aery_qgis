import urllib.request
import urllib.parse
import json

class WebSearchTool:
    """Tool that allows the agent to search the internet for documentation, APIs, or geodata."""
    
    name = "search_web"
    description = "Search the internet for information, documentation, or tutorials."
    
    def execute(self, params: dict) -> dict:
        query = params.get("query", "")
        if not query:
            return {"type": "text", "text": "Error: No search query provided."}
            
        try:
            # We use duckduckgo HTML scraping as a lightweight fallback 
            # if a proper API key (like Exa/Tavily) isn't configured in Aery
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            req = urllib.request.Request(
                url, 
                data=None, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                from aery_plugin.web_caps import read_capped_text
                html = read_capped_text(response)
                
            # Extremely basic text extraction to avoid adding BeautifulSoup dependency
            import re
            results = []
            snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL)
            
            for snippet in snippets[:5]:
                clean_snippet = re.sub('<[^<]+>', '', snippet).strip()
                if clean_snippet:
                    results.append(clean_snippet)
            
            if not results:
                return {"type": "text", "text": "Search completed but no snippets found."}
                
            output = "Web Search Results:\n\n" + "\n\n".join(f"- {res}" for res in results)
            return {"type": "text", "text": output}
            
        except Exception as e:
            return {"type": "text", "text": f"Web Search Error: {str(e)}"}
