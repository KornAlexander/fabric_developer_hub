for port in 5000 8000 60006; do
  echo "--- PORT $port ---"
  for path in /api/catalogs/agents /api/agents /api/catalogs/architectures; do
    echo "Endpoint: $path"
    response=$(curl -s "http://localhost:$port$path")
    if [ -z "$response" ]; then
      echo "No response"
      continue
    fi
    
    if [[ "$path" == *"/agents" ]]; then
      if echo "$response" | grep -Ei "generalist|orchestrator" > /dev/null; then
        echo "Found generalist/orchestrator: Yes"
      else
        echo "Found generalist/orchestrator: No"
      fi
    fi
    
    if [[ "$path" == *"/architectures" ]]; then
      ids=$(echo "$response" | grep -oP '"id":\s*"\K[^"]+' | tr '\n' ',' | sed 's/,$//')
      echo "Architecture IDs: $ids"
    fi
    echo "HTTP Status: $(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$port$path")"
    echo ""
  done
done
