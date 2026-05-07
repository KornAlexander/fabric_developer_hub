#!/bin/bash
WORKSPACE_ID="8bdca8af-1db1-4fd8-9564-0c98b4dbdffc"
REPORT_ID="cae2e0c7-af9d-43bb-82ee-8722c7a7b1b2"
EXPORT_ID="MS9CbG9iSWRWMi0yODFjNGZhZi1iZGY1LTRmNGItODdiYy1jMmMyZDEyNjk2MjM4QlFUcnBXZ2lHWjhOQUJ0dEJMUlV5NUZ1WlZOWVFtNGhFVWs4NDgybE4wPS4="
PDF_PATH="/home/lukaszobst/Fabric ClawHub/Developer Hub/docs/screenshots/fabric-inventory-report-live.pdf"
PNG_PATH="/home/lukaszobst/Fabric ClawHub/Developer Hub/docs/screenshots/fabric-inventory-report-live.png"

echo "Getting Power BI token..."
TOKEN=$(az account get-access-token --resource https://analysis.windows.net/powerbi/api --query accessToken -o tsv)

if [ -z "$TOKEN" ]; then
    echo "Failed to get token"
    exit 1
fi

URL="https://api.powerbi.com/v1.0/myorg/groups/$WORKSPACE_ID/reports/$REPORT_ID/exports/$EXPORT_ID"

MAX_ATTEMPTS=40
ATTEMPT=0

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    ATTEMPT=$((ATTEMPT+1))
    echo "Polling attempt $ATTEMPT..."
    
    RESPONSE=$(curl -s -X GET "$URL" -H "Authorization: Bearer $TOKEN")
    STATUS=$(echo "$RESPONSE" | grep -oP '"status":\s*"\K[^"]+')
    
    echo "Current status: $STATUS"
    
    if [ "$STATUS" == "Succeeded" ]; then
        echo "Export succeeded. Downloading file..."
        curl -s -X GET "$URL/file" -H "Authorization: Bearer $TOKEN" --output "$PDF_PATH"
        
        echo "Final status JSON:"
        echo "$RESPONSE"
        
        echo "File info:"
        ls -l "$PDF_PATH"
        file "$PDF_PATH"
        
        if command -v pdftoppm > /dev/null; then
            echo "Converting to PNG..."
            pdftoppm -f 1 -l 1 -png "$PDF_PATH" "${PNG_PATH%.png}"
            # pdftoppm adds -1 to the filename
            mv "${PNG_PATH%.png}-1.png" "$PNG_PATH"
            ls -l "$PNG_PATH"
            file "$PNG_PATH"
        else
            echo "pdftoppm not found, skipping conversion."
        fi
        exit 0
    elif [ "$STATUS" == "Failed" ]; then
        echo "Export failed."
        echo "$RESPONSE"
        exit 1
    fi
    
    sleep 15
done

echo "Polling timed out after 10 minutes."
exit 1
