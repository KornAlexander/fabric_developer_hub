#!/bin/bash
WORKSPACE_ID="8bdca8af-1db1-4fd8-9564-0c98b4dbdffc"
REPORT_ID="cae2e0c7-af9d-43bb-82ee-8722c7a7b1b2"
EXPORT_ID="MS9CbG9iSWRWMi0yODFjNGZhZi1iZGY1LTRmNGItODdiYy1jMmMyZDEyNjk2MjM4QlFUcnBXZ2lHWjhOQUJ0dEJMUlV5NUZ1WlZOWVFtNGhFVWs4NDgybE4wPS4="
PDF_PATH="/home/lukaszobst/Fabric ClawHub/Developer Hub/docs/screenshots/fabric-inventory-report-live.pdf"
PNG_PATH="/home/lukaszobst/Fabric ClawHub/Developer Hub/docs/screenshots/fabric-inventory-report-live.png"

echo "Getting Power BI token..."
TOKEN=$(az account get-access-token --resource https://analysis.windows.net/powerbi/api --query accessToken -o tsv)

URL="https://api.powerbi.com/v1.0/myorg/groups/$WORKSPACE_ID/reports/$REPORT_ID/exports/$EXPORT_ID"

while true; do
    echo "Checking status..."
    RESPONSE=$(curl -s -X GET "$URL" -H "Authorization: Bearer $TOKEN")
    STATUS=$(echo "$RESPONSE" | grep -oP '"status":\s*"\K[^"]+')
    
    echo "Current status: $STATUS"
    
    if [ "$STATUS" == "Succeeded" ]; then
        echo "Export succeeded. Downloading file..."
        curl -L -s -X GET "$URL/file" -H "Authorization: Bearer $TOKEN" --output "$PDF_PATH"
        
        # Check if actually a PDF
        if file "$PDF_PATH" | grep -q "PDF document"; then
            echo "Successfully downloaded PDF."
            echo "Final status JSON: $RESPONSE"
            ls -l "$PDF_PATH"
            file "$PDF_PATH"
            
            if command -v pdftoppm > /dev/null; then
                pdftoppm -f 1 -l 1 -png "$PDF_PATH" "${PNG_PATH%.png}"
                mv "${PNG_PATH%.png}-1.png" "$PNG_PATH"
                ls -l "$PNG_PATH"
                file "$PNG_PATH"
            else
                echo "pdftoppm not found."
            fi
            exit 0
        else
            echo "Download returned non-PDF content (possibly rate limited). Waiting 60s..."
            cat "$PDF_PATH"
            echo ""
        fi
    elif [ "$STATUS" == "Failed" ]; then
        echo "Export failed: $RESPONSE"
        exit 1
    fi
    
    echo "Waiting 30s..."
    sleep 30
done
