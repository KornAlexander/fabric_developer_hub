import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
    Button,
    Textarea,
    Spinner,
    Dropdown,
    Option,
    Text,
    Badge,
    Tooltip,
} from '@fluentui/react-components';
import {
    Send24Regular,
    Bot24Regular,
    Person24Regular,
    SignOut24Regular,
    ArrowSync24Regular,
    Dismiss24Regular,
    Open24Regular,
    ChatSparkleFilled,
} from '@fluentui/react-icons';
import { WorkloadClientAPI } from '@ms-fabric/workload-client';
import { callAuthAcquireAccessToken } from '../../controller/SampleWorkloadController';
import { renderMessageWithCards, WorkloadClientContext } from './ItemCard';

declare const process: {
    env: Record<string, string | undefined>;
};

const BACKEND_URL = process.env.WORKLOAD_BE_URL || 'http://localhost:5000';

interface ChatMessage {
    role: 'user' | 'assistant' | 'system';
    content: string;
}

interface ModelInfo {
    id: string;
    name: string;
    publisher?: string;
}

interface DeviceFlowState {
    userCode: string;
    verificationUri: string;
    deviceCode: string;
    interval: number;
    expiresIn: number;
}

export interface ChatPanelProps {
    isOpen: boolean;
    onClose: () => void;
    /** Optional context to inject into the system prompt */
    workloadContext?: string;
    /** Workload client for acquiring Fabric tokens for MCP tool execution */
    workloadClient?: WorkloadClientAPI;
}

export const ChatPanel: React.FC<ChatPanelProps> = ({ isOpen, onClose, workloadContext, workloadClient }) => {
    const [githubToken, setGithubToken] = useState<string | null>(
        () => sessionStorage.getItem('github_token')
    );
    const [githubUser, setGithubUser] = useState<string | null>(
        () => sessionStorage.getItem('github_user')
    );
    const [deviceFlow, setDeviceFlow] = useState<DeviceFlowState | null>(null);
    const [isPolling, setIsPolling] = useState(false);
    const [codeCopied, setCodeCopied] = useState(false);

    const [models, setModels] = useState<ModelInfo[]>([]);
    const [selectedModel, setSelectedModel] = useState<string>('gpt-4o');
    const [isLoadingModels, setIsLoadingModels] = useState(false);

    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [input, setInput] = useState('');
    const [isStreaming, setIsStreaming] = useState(false);
    const [toolStatus, setToolStatus] = useState<string | null>(null);

    const messagesEndRef = useRef<HTMLDivElement>(null);
    const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

    // Auto-scroll to bottom
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    // Load models when token is available
    useEffect(() => {
        if (githubToken) {
            loadModels();
        }
    }, [githubToken]);

    // Cleanup polling on unmount
    useEffect(() => {
        return () => {
            if (pollingRef.current) clearInterval(pollingRef.current);
        };
    }, []);

    const loadModels = async () => {
        if (!githubToken) return;
        setIsLoadingModels(true);
        try {
            const resp = await fetch(`${BACKEND_URL}/api/github/models`, {
                headers: { 'Authorization': `Bearer ${githubToken}` },
            });
            if (resp.ok) {
                const data = await resp.json();
                setModels(data.models || []);
            }
        } catch (e) {
            console.error('Failed to load models:', e);
        } finally {
            setIsLoadingModels(false);
        }
    };

    // --- GitHub Device Flow ---

    const startDeviceFlow = async () => {
        try {
            const resp = await fetch(`${BACKEND_URL}/api/github/device-code`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            if (!resp.ok) throw new Error('Failed to start device flow');
            const data = await resp.json();
            setDeviceFlow({
                userCode: data.user_code,
                verificationUri: data.verification_uri,
                deviceCode: data.device_code,
                interval: data.interval,
                expiresIn: data.expires_in,
            });
            // Auto-copy code to clipboard
            try {
                await navigator.clipboard.writeText(data.user_code);
                setCodeCopied(true);
            } catch { /* clipboard may not be available */ }
            startPolling(data.device_code, data.interval);
        } catch (e) {
            console.error('Device flow error:', e);
        }
    };

    const startPolling = (deviceCode: string, interval: number) => {
        setIsPolling(true);
        pollingRef.current = setInterval(async () => {
            try {
                const resp = await fetch(`${BACKEND_URL}/api/github/poll-token`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ device_code: deviceCode }),
                });
                const data = await resp.json();

                if (data.status === 'complete' && data.access_token) {
                    if (pollingRef.current) clearInterval(pollingRef.current);
                    setIsPolling(false);
                    setDeviceFlow(null);
                    setGithubToken(data.access_token);
                    setGithubUser(data.github_user || 'GitHub User');
                    sessionStorage.setItem('github_token', data.access_token);
                    sessionStorage.setItem('github_user', data.github_user || '');
                } else if (data.status === 'expired' || data.status === 'error') {
                    if (pollingRef.current) clearInterval(pollingRef.current);
                    setIsPolling(false);
                    setDeviceFlow(null);
                }
            } catch {
                // network error, keep polling
            }
        }, (interval + 1) * 1000);
    };

    const signOut = () => {
        setGithubToken(null);
        setGithubUser(null);
        setModels([]);
        setMessages([]);
        sessionStorage.removeItem('github_token');
        sessionStorage.removeItem('github_user');
    };

    // --- Chat ---

    const sendMessage = useCallback(async () => {
        if (!input.trim() || !githubToken || isStreaming) return;

        const userMessage: ChatMessage = { role: 'user', content: input.trim() };
        const newMessages = [...messages, userMessage];
        setMessages(newMessages);
        setInput('');
        setIsStreaming(true);
        setToolStatus(null);

        // Build messages with system prompt
        const systemMessages: ChatMessage[] = [];
        if (workloadContext) {
            systemMessages.push({
                role: 'system',
                content: `You are a helpful assistant integrated into a Microsoft Fabric workload called ClawHub. ${workloadContext}`,
            });
        }

        const assistantMessage: ChatMessage = { role: 'assistant', content: '' };
        setMessages([...newMessages, assistantMessage]);

        // Acquire Fabric token for MCP tool execution (best-effort)
        let fabricToken: string | null = null;
        if (workloadClient) {
            try {
                const accessToken = await callAuthAcquireAccessToken(workloadClient);
                fabricToken = accessToken.token;
            } catch (e) {
                console.warn('Could not acquire Fabric token for tools:', e);
            }
        }

        try {
            const headers: Record<string, string> = {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${githubToken}`,
            };
            if (fabricToken) {
                headers['X-Fabric-Token'] = `Bearer ${fabricToken}`;
            }

            const resp = await fetch(`${BACKEND_URL}/api/github/chat/completions`, {
                method: 'POST',
                headers,
                body: JSON.stringify({
                    model: selectedModel,
                    messages: [...systemMessages, ...newMessages],
                    stream: true,
                    max_tokens: 4096,
                    temperature: 0.7,
                    tools_enabled: true,
                }),
            });

            if (!resp.ok) {
                const errText = await resp.text();
                setMessages(prev => {
                    const updated = [...prev];
                    updated[updated.length - 1] = { role: 'assistant', content: `Error: ${errText}` };
                    return updated;
                });
                setIsStreaming(false);
                setToolStatus(null);
                return;
            }

            const reader = resp.body?.getReader();
            const decoder = new TextDecoder();
            let accumulated = '';

            if (reader) {
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    const chunk = decoder.decode(value, { stream: true });
                    const lines = chunk.split('\n');

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            const jsonStr = line.slice(6).trim();
                            if (jsonStr === '[DONE]') continue;
                            try {
                                const parsed = JSON.parse(jsonStr);

                                // Tool-call status events from agentic loop
                                if (parsed.type === 'status') {
                                    setToolStatus(parsed.content);
                                    continue;
                                }

                                // Stream delta (final response or tool-free response)
                                const delta = parsed.choices?.[0]?.delta?.content;
                                if (delta) {
                                    accumulated += delta;
                                    setToolStatus(null);
                                    setMessages(prev => {
                                        const updated = [...prev];
                                        updated[updated.length - 1] = { role: 'assistant', content: accumulated };
                                        return updated;
                                    });
                                }
                            } catch {
                                // skip non-JSON lines
                            }
                        }
                    }
                }
            }
        } catch (e) {
            setMessages(prev => {
                const updated = [...prev];
                updated[updated.length - 1] = { role: 'assistant', content: `Error: ${String(e)}` };
                return updated;
            });
        } finally {
            setIsStreaming(false);
            setToolStatus(null);
        }
    }, [input, githubToken, isStreaming, messages, selectedModel, workloadContext, workloadClient]);

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    };

    if (!isOpen) return null;

    return (
        <div className="chat-panel">
            {/* Header */}
            <div className="chat-panel-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <ChatSparkleFilled style={{ fontSize: 20 }} />
                    <Text weight="semibold" size={400}>ClawHub Chat</Text>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    {githubUser && (
                        <Tooltip content="Sign out" relationship="label">
                            <Button
                                size="small"
                                appearance="subtle"
                                icon={<SignOut24Regular />}
                                onClick={signOut}
                            />
                        </Tooltip>
                    )}
                    <Button size="small" appearance="subtle" icon={<Dismiss24Regular />} onClick={onClose} />
                </div>
            </div>

            {/* Sign-in or Chat */}
            {!githubToken ? (
                <div className="chat-panel-signin">
                    {!deviceFlow ? (
                        <>
                            <Bot24Regular style={{ fontSize: 48, color: '#6264A7', marginBottom: 12 }} />
                            <Text size={300} align="center">
                                Sign in with your GitHub account to use AI chat powered by GitHub Copilot.
                            </Text>
                            <Button
                                appearance="primary"
                                onClick={startDeviceFlow}
                                style={{ marginTop: 16 }}
                            >
                                Sign in with GitHub
                            </Button>
                        </>
                    ) : (
                        <>
                            <Text size={300} align="center">
                                {codeCopied && <><strong>Code copied!</strong>{' '}</>}
                                Open{' '}
                                <a
                                    href={`${deviceFlow.verificationUri}?user_code=${deviceFlow.userCode}`}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    style={{ color: '#0078d4', fontWeight: 600 }}
                                >
                                    github.com/login/device
                                </a>
                                {codeCopied ? ' and paste the code:' : ' and enter this code:'}
                            </Text>
                            <div className="chat-panel-device-code">
                                <Text size={600} weight="bold" font="monospace">
                                    {deviceFlow.userCode}
                                </Text>
                            </div>
                            <Button
                                appearance="primary"
                                icon={<Open24Regular />}
                                onClick={() => {
                                    navigator.clipboard?.writeText(deviceFlow.userCode).then(() => setCodeCopied(true));
                                }}
                            >
                                {codeCopied ? 'Copied!' : 'Copy code'}
                            </Button>
                            {isPolling && (
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12 }}>
                                    <Spinner size="tiny" />
                                    <Text size={200}>Waiting for authorization...</Text>
                                </div>
                            )}
                        </>
                    )}
                </div>
            ) : (
                <>
                    {/* Model selector */}
                    <div className="chat-panel-model-bar">
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1 }}>
                            <Dropdown
                                size="small"
                                placeholder="Select model"
                                value={models.find(m => m.id === selectedModel)?.name || selectedModel}
                                onOptionSelect={(_, data) => {
                                    if (data.optionValue) setSelectedModel(data.optionValue);
                                }}
                                style={{ minWidth: 160, flex: 1 }}
                            >
                                {models.map(m => (
                                    <Option key={m.id} value={m.id} text={m.name}>
                                        {m.name}
                                    </Option>
                                ))}
                            </Dropdown>
                            <Tooltip content="Refresh models" relationship="label">
                                <Button
                                    size="small"
                                    appearance="subtle"
                                    icon={<ArrowSync24Regular />}
                                    onClick={loadModels}
                                    disabled={isLoadingModels}
                                />
                            </Tooltip>
                        </div>
                        {githubUser && (
                            <Badge appearance="outline" size="small" color="brand">
                                {githubUser}
                            </Badge>
                        )}
                    </div>

                    {/* Messages */}
                    <WorkloadClientContext.Provider value={workloadClient || null}>
                    <div className="chat-panel-messages">
                        {messages.length === 0 && (
                            <div className="chat-panel-empty">
                                <Bot24Regular style={{ fontSize: 32, color: '#999' }} />
                                <Text size={200} style={{ color: '#999' }}>
                                    Ask anything about your data or workload.
                                </Text>
                            </div>
                        )}
                        {messages.map((msg, i) => (
                            <div key={i} className={`chat-message chat-message-${msg.role}`}>
                                <div className="chat-message-icon">
                                    {msg.role === 'user' ? (
                                        <Person24Regular style={{ fontSize: 16 }} />
                                    ) : (
                                        <Bot24Regular style={{ fontSize: 16 }} />
                                    )}
                                </div>
                                <div className="chat-message-content">
                                    <Text size={200} style={{ whiteSpace: 'pre-wrap' }}>
                                        {msg.role === 'assistant'
                                            ? renderMessageWithCards(msg.content)
                                            : msg.content}
                                        {isStreaming && i === messages.length - 1 && msg.role === 'assistant' && (
                                            <span className="chat-cursor">▊</span>
                                        )}
                                    </Text>
                                </div>
                            </div>
                        ))}
                        <div ref={messagesEndRef} />
                    </div>
                    </WorkloadClientContext.Provider>

                    {/* Tool status indicator */}
                    {toolStatus && (
                        <div className="chat-tool-status">
                            <Spinner size="tiny" />
                            <Text size={200}>{toolStatus}</Text>
                        </div>
                    )}

                    {/* Input */}
                    <div className="chat-panel-input">
                        <Textarea
                            size="small"
                            placeholder="Ask a question..."
                            value={input}
                            onChange={(_, data) => setInput(data.value)}
                            onKeyDown={handleKeyDown}
                            disabled={isStreaming}
                            resize="none"
                            style={{ flex: 1 }}
                            rows={2}
                        />
                        <Button
                            appearance="primary"
                            icon={isStreaming ? <Spinner size="tiny" /> : <Send24Regular />}
                            onClick={sendMessage}
                            disabled={!input.trim() || isStreaming}
                            size="small"
                        />
                    </div>
                </>
            )}
        </div>
    );
};
