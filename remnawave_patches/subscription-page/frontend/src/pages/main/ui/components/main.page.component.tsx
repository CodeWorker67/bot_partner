/**
 * Замена для: remnawave/subscription-page → frontend/src/pages/main/ui/components/main.page.component.tsx
 *
 * URL и ключ берутся из переменных Vite (префикс VITE_) — их задают при СБОРКЕ фронта.
 *
 * 1) В /opt/remnawave/.env.sub добавьте (без кавычек, без пробелов вокруг =):
 *    VITE_SUB_PAGE_PAY_API_BASE=http://btg.speedgamer.top
 *    VITE_SUB_PAGE_PAY_API_KEY=<тот же SUB_PAGE_API_KEY, что в .env бота>
 *
 *    Если VITE_SUB_PAGE_PAY_API_BASE не задан, по умолчанию используется http://btg.speedgamer.top
 *
 * 2) Сборка frontend (подставьте путь к клону subscription-page):
 *    docker run --rm -it --env-file /opt/remnawave/.env.sub \
 *      -v /opt/subscription-page/frontend:/work -w /work \
 *      -e NODE_OPTIONS=--max-old-space-size=4096 \
 *      node:24-bookworm-slim bash -lc "npm ci && npm run start:build"
 *
 * 3) docker build образа subscription-page и compose up, как раньше.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
    Box,
    Button,
    Card,
    Center,
    Collapse,
    Container,
    Group,
    Image,
    Modal,
    SimpleGrid,
    Stack,
    Text,
    Title,
    UnstyledButton
} from '@mantine/core'
import { TSubscriptionPagePlatformKey } from '@remnawave/subscription-page-types'

import {
    AccordionBlockRenderer,
    CardsBlockRenderer,
    InstallationGuideConnector,
    MinimalBlockRenderer,
    RawKeysWidget,
    SubscriptionInfoCardsWidget,
    SubscriptionInfoCollapsedWidget,
    SubscriptionInfoExpandedWidget,
    SubscriptionLinkWidget,
    TimelineBlockRenderer
} from '@widgets/main'
import { useAppConfig, useAppConfigStoreActions, useCurrentLang } from '@entities/app-config-store'
import { useSubscription } from '@entities/subscription-info-store'
import { LanguagePicker } from '@shared/ui/language-picker/language-picker.shared'
import { Page, RemnawaveLogo } from '@shared/ui'

const DEFAULT_PAY_API_BASE = 'http://btg.speedgamer.top'

function subPagePayFromBuild(): { apiBase: string; apiKey: string } {
    return {
        apiBase: String(import.meta.env.VITE_SUB_PAGE_PAY_API_BASE ?? DEFAULT_PAY_API_BASE).trim(),
        apiKey: String(import.meta.env.VITE_SUB_PAGE_PAY_API_KEY ?? '').trim()
    }
}

type DurationId =
    | 'm1_d3'
    | 'm3_d3'
    | 'm6_d3'
    | 'm12_d3'
    | 'm1_d5'
    | 'm3_d5'
    | 'm6_d5'
    | 'm12_d5'
    | 'm1_d10'
    | 'm3_d10'
    | 'm6_d10'
    | 'm12_d10'

type PayMethodId = 'fk_sbp' | 'fk_card' | 'stars' | 'cryptobot'

const PAY_METHODS: ReadonlyArray<{ id: PayMethodId; label: string }> = [
    { id: 'fk_sbp', label: 'СБП' },
    { id: 'fk_card', label: 'Карты РФ' },
    { id: 'stars', label: 'Telegram Stars' },
    { id: 'cryptobot', label: 'Telegram Cryptobot' }
]

type DeviceTier = 3 | 5 | 10

function deviceTierFromUsername(username: string): DeviceTier {
    if (username.endsWith('_10')) return 10
    if (username.endsWith('_3')) return 3
    return 5
}

/** user_id: числовая часть username; снимаются суффиксы `_white`, `_10`, `_3`. */
function parseSubPageUserId(username: string): number | null {
    let base = username
    if (base.endsWith('_white')) base = base.slice(0, -'_white'.length)
    if (base.endsWith('_10')) base = base.slice(0, -'_10'.length)
    else if (base.endsWith('_3')) base = base.slice(0, -'_3'.length)
    const n = Number.parseInt(base, 10)
    return Number.isFinite(n) ? n : null
}

function payBlockTitle(tier: DeviceTier): string {
    if (tier === 3) return 'Оплата подписки на 3 устройства'
    if (tier === 10) return 'Оплата подписки на 10 устройств'
    return 'Оплата подписки на 5 устройств'
}

const TARIFF_ROWS: Record<
    DeviceTier,
    ReadonlyArray<{ label: string; duration: DurationId }>
> = {
    3: [
        { label: '1 месяц — 199 ₽', duration: 'm1_d3' },
        { label: '3 месяца — 499 ₽ (выгода −16%)', duration: 'm3_d3' },
        { label: '6 месяцев — 999 ₽ (выгода −16%)', duration: 'm6_d3' },
        { label: '12 месяцев — 1188 ₽ (выгода −50%)', duration: 'm12_d3' }
    ],
    5: [
        { label: '1 месяц — 299 ₽', duration: 'm1_d5' },
        { label: '3 месяца — 749 ₽ (выгода −16%)', duration: 'm3_d5' },
        { label: '6 месяцев — 1349 ₽ (выгода −25%)', duration: 'm6_d5' },
        { label: '12 месяцев — 1799 ₽ (выгода −50%)', duration: 'm12_d5' }
    ],
    10: [
        { label: '1 месяц — 659 ₽', duration: 'm1_d10' },
        { label: '3 месяца — 1349 ₽ (выгода −32%)', duration: 'm3_d10' },
        { label: '6 месяцев — 2399 ₽ (выгода −39%)', duration: 'm6_d10' },
        { label: '12 месяцев — 3239 ₽ (выгода −59%)', duration: 'm12_d10' }
    ]
}

function SubscriptionPayBlock({ isMobile }: { isMobile: boolean }) {
    const { user } = useSubscription()
    const tier = useMemo(() => deviceTierFromUsername(user.username), [user.username])
    const userId = useMemo(() => parseSubPageUserId(user.username), [user.username])
    const payCfg = useMemo(() => subPagePayFromBuild(), [])
    const subscriptionStillActive = useMemo(() => {
        if (user.userStatus !== 'ACTIVE') return false
        if (user.daysLeft == null) return true
        return Number(user.daysLeft) > 0
    }, [user.daysLeft, user.userStatus])

    const [payExpanded, setPayExpanded] = useState(() => !subscriptionStillActive)
    const [modalOpen, setModalOpen] = useState(false)
    const [pickedDuration, setPickedDuration] = useState<DurationId | null>(null)
    const [busyMethod, setBusyMethod] = useState<PayMethodId | null>(null)
    const [errorText, setErrorText] = useState<string | null>(null)

    useEffect(() => {
        setPayExpanded(!subscriptionStillActive)
    }, [subscriptionStillActive])

    const openPay = useCallback((d: DurationId) => {
        setErrorText(null)
        setPickedDuration(d)
        setModalOpen(true)
    }, [])

    const closeModal = useCallback(() => {
        if (busyMethod) return
        setModalOpen(false)
        setPickedDuration(null)
        setErrorText(null)
    }, [busyMethod])

    const submitPay = useCallback(
        async (method: PayMethodId) => {
            if (userId == null || pickedDuration == null) return
            if (!payCfg.apiKey) return
            setBusyMethod(method)
            setErrorText(null)
            const url = `${payCfg.apiBase.replace(/\/$/, '')}/api/v1/sub_page/pay/${method}`
            try {
                const res = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Sub-Page-Api-Key': payCfg.apiKey
                    },
                    body: JSON.stringify({ user_id: userId, duration: pickedDuration })
                })
                const data: unknown = await res.json().catch(() => ({}))
                if (!res.ok) {
                    const msg =
                        typeof data === 'object' &&
                        data !== null &&
                        'detail' in data &&
                        typeof (data as { detail?: unknown }).detail === 'string'
                            ? (data as { detail: string }).detail
                            : `Ошибка ${res.status}`
                    setErrorText(msg)
                    return
                }
                const obj = data as { payment_url?: string; bot_url?: string }
                const redirect = obj.payment_url || obj.bot_url
                if (redirect && typeof redirect === 'string') {
                    window.location.assign(redirect)
                    return
                }
                setErrorText('В ответе нет ссылки для перехода')
            } catch {
                setErrorText('Сеть недоступна или сервер не ответил')
            } finally {
                setBusyMethod(null)
            }
        },
        [pickedDuration, payCfg.apiBase, payCfg.apiKey, userId]
    )

    if (!payCfg.apiKey) {
        if (subscriptionStillActive) return null
        return (
            <Card p="md" radius="lg" withBorder>
                <Text c="dimmed" size="sm">
                    Оплата: не задан VITE_SUB_PAGE_PAY_API_KEY при сборке фронта. Добавьте его в .env.sub и
                    пересоберите образ (см. комментарий в начале main.page.component.tsx).
                </Text>
            </Card>
        )
    }

    if (userId == null) {
        if (subscriptionStillActive) return null
        return (
            <Card p="md" radius="lg" withBorder>
                <Text c="dimmed" size="sm">
                    Оплата: не удалось определить user_id из имени пользователя подписки.
                </Text>
            </Card>
        )
    }

    const rows = TARIFF_ROWS[tier]

    return (
        <>
            <Card p="md" radius="lg" withBorder>
                <Stack gap="md">
                    <UnstyledButton onClick={() => setPayExpanded((v) => !v)} w="100%">
                        <Group gap="sm" justify="space-between" wrap="nowrap">
                            <Title c="white" order={5} style={{ flex: 1, textAlign: 'left' }}>
                                {payBlockTitle(tier)}
                            </Title>
                            <Box
                                aria-hidden
                                c="dimmed"
                                style={{
                                    flexShrink: 0,
                                    fontSize: 12,
                                    lineHeight: 1,
                                    transform: payExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
                                    transition: 'transform 200ms ease'
                                }}
                            >
                                ▼
                            </Box>
                        </Group>
                    </UnstyledButton>
                    <Collapse in={payExpanded}>
                        <Stack gap="sm">
                            {rows.map((row) => (
                                <Button
                                    key={row.duration}
                                    fullWidth
                                    justify="space-between"
                                    onClick={() => openPay(row.duration)}
                                    radius="md"
                                    size={isMobile ? 'sm' : 'md'}
                                    variant="light"
                                >
                                    <Text fw={500} size="sm" style={{ textAlign: 'left' }}>
                                        {row.label}
                                    </Text>
                                </Button>
                            ))}
                        </Stack>
                    </Collapse>
                </Stack>
            </Card>

            <Modal
                centered
                onClose={closeModal}
                opened={modalOpen}
                radius="lg"
                title="Выберите способ оплаты"
            >
                <Stack gap="sm">
                    {errorText ? (
                        <Text c="red" size="sm">
                            {errorText}
                        </Text>
                    ) : null}
                    <SimpleGrid cols={1} spacing="xs">
                        {PAY_METHODS.map((m) => (
                            <Button
                                key={m.id}
                                loading={busyMethod === m.id}
                                onClick={() => void submitPay(m.id)}
                                radius="md"
                                variant="filled"
                            >
                                {m.label}
                            </Button>
                        ))}
                    </SimpleGrid>
                    <Button disabled={!!busyMethod} onClick={closeModal} variant="subtle">
                        Отмена
                    </Button>
                </Stack>
            </Modal>
        </>
    )
}

interface IMainPageComponentProps {
    isMobile: boolean
    platform: TSubscriptionPagePlatformKey | undefined
}

const BLOCK_RENDERERS = {
    cards: CardsBlockRenderer,
    timeline: TimelineBlockRenderer,
    accordion: AccordionBlockRenderer,
    minimal: MinimalBlockRenderer
} as const

const SUBSCRIPTION_INFO_BLOCK_RENDERERS = {
    cards: SubscriptionInfoCardsWidget,
    collapsed: SubscriptionInfoCollapsedWidget,
    expanded: SubscriptionInfoExpandedWidget,
    hidden: null
} as const

export const MainPageComponent = ({ isMobile, platform }: IMainPageComponentProps) => {
    const config = useAppConfig()
    const currentLang = useCurrentLang()
    const { setLanguage } = useAppConfigStoreActions()

    const brandName = config.brandingSettings.title
    let hasCustomLogo = !!config.brandingSettings.logoUrl

    if (hasCustomLogo) {
        if (config.brandingSettings.logoUrl.includes('docs.rw')) {
            hasCustomLogo = false
        }
    }

    const hasPlatformApps: Record<TSubscriptionPagePlatformKey, boolean> = {
        ios: Boolean(config.platforms.ios?.apps.length),
        android: Boolean(config.platforms.android?.apps.length),
        linux: Boolean(config.platforms.linux?.apps.length),
        macos: Boolean(config.platforms.macos?.apps.length),
        windows: Boolean(config.platforms.windows?.apps.length),
        androidTV: Boolean(config.platforms.androidTV?.apps.length),
        appleTV: Boolean(config.platforms.appleTV?.apps.length)
    }

    const atLeastOnePlatformApp = Object.values(hasPlatformApps).some((value) => value)

    const SubscriptionInfoBlockRenderer =
        SUBSCRIPTION_INFO_BLOCK_RENDERERS[config.uiConfig.subscriptionInfoBlockType]

    return (
        <Page>
            <Box className="header-wrapper" py="md">
                <Container maw={1200} px={{ base: 'md', sm: 'lg', md: 'xl' }}>
                    <Group justify="space-between">
                        <Group gap="sm" style={{ userSelect: 'none' }} wrap="nowrap">
                            {hasCustomLogo ? (
                                <Image
                                    alt="logo"
                                    fit="contain"
                                    src={config.brandingSettings.logoUrl}
                                    style={{
                                        width: '32px',
                                        height: '32px',
                                        flexShrink: 0
                                    }}
                                />
                            ) : (
                                <RemnawaveLogo c="cyan" size={32} />
                            )}
                            <Title
                                c={hasCustomLogo ? 'white' : 'cyan'}
                                fw={700}
                                order={4}
                                size="lg"
                            >
                                {brandName}
                            </Title>
                        </Group>

                        <SubscriptionLinkWidget
                            hideGetLink={config.baseSettings.hideGetLinkButton}
                            supportUrl={config.brandingSettings.supportUrl}
                        />
                    </Group>
                </Container>
            </Box>

            <Container
                maw={1200}
                px={{ base: 'md', sm: 'lg', md: 'xl' }}
                py="xl"
                style={{ position: 'relative', zIndex: 1 }}
            >
                <Stack gap="xl">
                    {SubscriptionInfoBlockRenderer && (
                        <SubscriptionInfoBlockRenderer isMobile={isMobile} />
                    )}

                    <SubscriptionPayBlock isMobile={isMobile} />

                    {atLeastOnePlatformApp && (
                        <InstallationGuideConnector
                            BlockRenderer={
                                BLOCK_RENDERERS[config.uiConfig.installationGuidesBlockType]
                            }
                            hasPlatformApps={hasPlatformApps}
                            isMobile={isMobile}
                            platform={platform}
                        />
                    )}

                    <RawKeysWidget isMobile={isMobile} />

                    <Center>
                        <LanguagePicker
                            currentLang={currentLang}
                            locales={config.locales}
                            onLanguageChange={setLanguage}
                        />
                    </Center>
                </Stack>
            </Container>
        </Page>
    )
}
