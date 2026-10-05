//Short-lived HS256 tokens issued by the host (the Django portal, or the dev endpoint below) and checked here.
//Claims: sub (user id), name, workspace, role, exp (unix seconds). Nothing else is trusted from the client.
import { createHmac, timingSafeEqual } from 'node:crypto';

export const ROLES = ['owner', 'editor', 'participant', 'viewer'];
const b64 = input => Buffer.from(input).toString('base64url');
const sign = (data, secret) => createHmac('sha256', secret).update(data).digest('base64url');

export function issueToken(claims, secret, ttlSeconds = 300) {
    if (!ROLES.includes(claims.role)) throw new Error(`invalid role "${claims.role}"`);
    const header = b64(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
    const body = b64(JSON.stringify({ ...claims, exp: Math.floor(Date.now() / 1000) + ttlSeconds }));
    return `${header}.${body}.${sign(`${header}.${body}`, secret)}`;
}

//Returns the claims, or throws Error('…') with a reason that is safe to log (never sent to the client in detail)
export function verifyToken(token, secret) {
    const parts = String(token ?? '').split('.');
    if (parts.length !== 3) throw new Error('malformed token');
    const [header, body, signature] = parts;
    const expected = Buffer.from(sign(`${header}.${body}`, secret));
    const given = Buffer.from(signature);
    if (expected.length !== given.length || !timingSafeEqual(expected, given)) throw new Error('bad signature');
    if (JSON.parse(Buffer.from(header, 'base64url')).alg !== 'HS256') throw new Error('unsupported algorithm');
    const claims = JSON.parse(Buffer.from(body, 'base64url'));
    if (typeof claims.exp !== 'number' || claims.exp * 1000 < Date.now()) throw new Error('expired');
    if (!claims.sub || !claims.workspace || !ROLES.includes(claims.role)) throw new Error('missing claims');
    return claims;
}

//Same spirit as the IA's config/segredos.py: refuse to start with a weak secret outside dev mode
export function checkSecret(secret, devAuth) {
    if (devAuth) return secret || 'dev-secret-not-for-production';
    if (!secret || secret.length < 32) throw new Error('AGORA_SYNC_SECRET must be set and at least 32 characters (or enable AGORA_DEV_AUTH=1 for local development)');
    return secret;
}
