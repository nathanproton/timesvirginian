# Search Logging System

This document describes the search logging system implemented in the Times Virginian application.

## Overview

The application now logs all search activity to both the console and a dedicated log file. This provides comprehensive tracking of user search behavior, system performance, and potential issues.

## Log File Location

Search logs are stored in:
```
logs/search_logs.txt
```

The logs directory is automatically created when the application starts.

## Log Format

Each log entry follows this format:
```
2024-01-15 10:30:45,123 - SEARCH: 'search query' | IP: 192.168.1.100 | Status: success | Results: 5 | User-Agent: Mozilla/5.0...
```

### Fields

- **Timestamp**: ISO format timestamp
- **Query**: The actual search text (sanitized)
- **IP**: User's IP address
- **Status**: Search result status (success, error, missing_input, invalid_input)
- **Results**: Number of results returned (for successful searches)
- **User-Agent**: Browser/client information

## Log Access

**Note**: All public web endpoints for viewing search logs have been removed for security purposes. Search logs are now only accessible through direct file system access to maintain privacy and security.

## Log Management

The system automatically logs all search activity to the file system:
- All search requests are logged with detailed information
- Logs are stored in `logs/search_logs.txt`
- Log files are not publicly accessible via web endpoints
- Manual log rotation may be needed if files grow too large

## Security Considerations

- Log files are stored locally and not exposed through web endpoints
- IP addresses are logged for security monitoring
- User agent strings are logged for debugging purposes
- Direct file system access is required to view logs

## Monitoring

The logging system provides insights into:
- **Usage Patterns**: Most common search terms, peak usage times
- **Performance**: Success/error rates, response times
- **Security**: Suspicious IP addresses, unusual search patterns
- **User Experience**: Failed searches, common error scenarios

## Example Usage

### View Logs Directly
```bash
# View recent logs
tail -100 logs/search_logs.txt

# View all logs
cat logs/search_logs.txt

# Search for specific patterns
grep "Status: error" logs/search_logs.txt
```

## Troubleshooting

### Log File Not Created
- Ensure the application has write permissions to the project directory
- Check that the `logs` directory exists
- Verify the application is running with proper permissions

### Empty Logs
- Confirm that search requests are reaching the `/submit` endpoint
- Check that the `log_search` function is being called
- Verify the search logger configuration

### Large Log Files
- Manually truncate log files when they become too large
- Monitor log file size regularly with `ls -lh logs/search_logs.txt`
- Consider implementing log rotation tools if needed 