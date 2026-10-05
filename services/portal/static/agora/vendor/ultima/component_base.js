

export class ComponentBase extends HTMLElement {



    static VERSION = '0.3.0';

    static LOADED_EVENT = "component-loaded";

    //Fired (bubbling, composed) when the template can't be loaded after every attempt. A parent
    //counts a failed child as settled, so one broken component doesn't leave its ancestors waiting forever.
    static ERROR_EVENT = "component-error";

    static MAX_LOAD_ATTEMPTS = 3; // Gives up after 3 failed attempts to load a resource

    //Fetched text per resolved URL (templates and CSS), shared by every instance of every component:
    //a list of 1000 components costs one request, not 1000. Only successes are cached, so a failed
    //fetch is retried by the next attempt/instance. Lives for the page's lifetime (reload to refresh).
    static #textCache = new Map();

    //Scripts referenced by templates are loaded once per resolved src and type, however many
    //instances (or components) reference them.
    static #scriptCache = new Map();

    static #fetchText(url) {
        const key = String(url);
        if (!ComponentBase.#textCache.has(key)) {
            const promise = fetch(url).then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                return response.text();
            });
            ComponentBase.#textCache.set(key, promise);
            promise.catch(() => ComponentBase.#textCache.delete(key));
        }
        return ComponentBase.#textCache.get(key);
    }



    #rootNode;

    #baseComponentChildren;

    #subclassUrl;
    #baseUrl;

    #loaded;
    #failed;

    #totalChildCount;
    #loadedChildCount;



    /**
     * To build a ComponentBase, pass an object with the component's properties and the URL of the
     * JavaScript class that inherits it.
     *
     * @param {Object} properties - Component properties
     * @param {string} properties.templateUrl - URL of the component's HTML template
     * @param {boolean} properties.shadowDom - true to use Shadow DOM, false to use the light DOM
     * @param {string} subclassUrl - URL of the JavaScript module that inherits ComponentBase (usually import.meta.url)
     */
    constructor(properties, subclassUrl) {
        super();

        // TODO: check that the properties object has the required fields
        this.#subclassUrl = subclassUrl;
        this.#baseUrl = ComponentBase.extractUrlPath(this.#subclassUrl);


        this.#loaded = false;
        this.#failed = false;

        this.#totalChildCount = 0;
        this.#loadedChildCount = 0;


        if (properties.shadowDom) {
            this.#rootNode = this.attachShadow({ mode: 'open' });
        } else {
            this.#rootNode = this;
        }

        this.loadTemplate(properties.templateUrl);
    }



    // Getter for rootNode
    get rootNode() {
        return this.#rootNode;
    }



    // Getter for loaded
    get loaded(){
        return this.#loaded;
    }

    // true if the template couldn't be loaded (see ERROR_EVENT)
    get failed(){
        return this.#failed;
    }



    /**
     * Override to run initialization once this component (its own template plus every descendant
     * ComponentBase) has finished loading — called directly at the exact point checkLoading()/
     * checkIfAllChildrenLoaded() determine that, so subclasses get it for free without subscribing
     * to LOADED_EVENT themselves and filtering it by composedPath()[0] (see whenLoaded() below for
     * why that filtering is still needed for outside listeners). Subclasses that override this must
     * call super.onLoad().
     */
    onLoad() {}

    /**
     * Promise-based equivalent of onLoad() for code outside the component's own class (e.g. the page
     * that uses it), where overriding onLoad() isn't an option. Resolves immediately if already
     * loaded; otherwise waits for this element's own LOADED_EVENT — filtering out the ones that
     * bubble up from descendants via composedPath()[0], since event.target gets retargeted to this
     * element when the descendant has its own Shadow Root.
     * @returns {Promise<void>}
     */
    whenLoaded() {
        if (this.#loaded) {
            return Promise.resolve();
        }

        return new Promise(resolve => {
            const listener = (event) => {
                if (event.composedPath()[0] !== this) {
                    return;
                }
                this.removeEventListener(ComponentBase.LOADED_EVENT, listener);
                resolve();
            };
            this.addEventListener(ComponentBase.LOADED_EVENT, listener);
        });
    }



    /**
     * Extracts the base path from a URL
     * @param {string} url - Full URL
     * @returns {string} Base path of the URL
     */
    static extractUrlPath(url) {
        return url.slice(0, url.lastIndexOf("/") + 1);
    }



    /**
     * Resolves a relative or absolute address
     * @param {string} address - Address to resolve
     * @param {string} baseUrl - Base URL used to resolve relative addresses
     * @returns {URL} Resolved URL
     */
    static resolveAddress(address, baseUrl) {
        try {
            return new URL(address);
        } catch (e) {
            return new URL(address, baseUrl);
        }
    }



    resolveAddress(address) {
        return ComponentBase.resolveAddress(address, this.#baseUrl);
    }



    /**
     * Loads the component's HTML template, retrying up to MAX_LOAD_ATTEMPTS times. A template that
     * can't be fetched (network error or non-2xx status) fires ERROR_EVENT. Failing CSS/scripts
     * referenced by a template that did load are logged, but don't prevent the component from loading.
     * @param {string} templateUrl - URL of the HTML template
     */
    async loadTemplate(templateUrl) {
        let pageText;
        let lastError;

        for (let attempt = 1; attempt <= ComponentBase.MAX_LOAD_ATTEMPTS; attempt++) {
            try {
                pageText = await ComponentBase.#fetchText(this.resolveAddress(templateUrl));
                break;
            } catch (error) {
                lastError = error;
            }
        }

        if (pageText === undefined) {
            console.error(`Error loading template: ${templateUrl} (${lastError.message}) after ${ComponentBase.MAX_LOAD_ATTEMPTS} attempts`);
            this.#failed = true;
            this.dispatchEvent(new CustomEvent(ComponentBase.ERROR_EVENT, { bubbles: true, composed: true, detail: lastError }));
            return false;
        }

        let template = document.createElement("template");
        template.innerHTML = pageText;

        let element = template.content.cloneNode(true);

        let hrefLinks = this.removeLinkTagsAndGetHrefs(element);
        let scripts = this.removeAndRetrieveScriptElements(element);

        // Fixes relative paths on tags like <img>, <a> and others
        this.fixRelativePaths(element);

        const results = await Promise.allSettled([
            ...hrefLinks.map(cssUrl => this.loadCSS(cssUrl)),
            ...scripts.map(script => this.loadScript(script))
        ]);

        results.filter(result => result.status === 'rejected')
            .forEach(() => console.error(`[${this.constructor.name}] a resource referenced by the template failed to load`));

        this.#rootNode.appendChild(element);
        this.observe();

        setTimeout(() => {
            this.checkLoading();
        });
    }

    fixRelativePaths(element) {
        const tagsToFix = {
            'img': 'src',
            'a': 'href',
            'audio': 'src',
            'video': 'src',
            'source': 'src',
            'iframe': 'src',
            'embed': 'src',
            'object': 'data',
            'track': 'src',
            'area': 'href',
            'meta': 'content',
            'link': 'href'
        };

        for (let tag in tagsToFix) {

            let attribute = tagsToFix[tag];

            element.querySelectorAll(tag).forEach(tagElement => {
                if (tag === 'link' && tagElement.getAttribute('rel') === 'stylesheet') {
                    return; // Skip CSS links
                }
                let attributeValue = tagElement.getAttribute(attribute);
                if (attributeValue) {
                    let fixedUrl = this.resolveAddress(attributeValue);
                    tagElement.setAttribute(attribute, fixedUrl);
                }
            });
        }
    }



    /**
     * Removes <link> tags from the template and returns their hrefs
     * @param {DocumentFragment} element - Loaded template
     * @returns {string[]} List of CSS file URLs
     */
    removeLinkTagsAndGetHrefs(element) {
        return Array.from(element.querySelectorAll("link")).map(linkElement => {
            let cssUrl = linkElement.getAttribute("href");
            linkElement.remove();
            return cssUrl;
        });
    }



    /**
     * Removes <script> tags from the template and returns their attributes
     * @param {DocumentFragment} element - Loaded template
     * @returns {Object[]} List of script element attributes
     */
    removeAndRetrieveScriptElements(element) {
        return Array.from(element.querySelectorAll("script")).map(scriptElement => {
            let scriptAttributes = {
                src: scriptElement.getAttribute("src"),
                type: scriptElement.getAttribute("type"),
                integrity: scriptElement.getAttribute("integrity"),
            };
            scriptElement.remove();
            return scriptAttributes;
        });
    }



    observe() {
        if (!this.resizeObserver) {
            this.resizeObserver = new ResizeObserver(entries => {
                entries.forEach(entry => {
                    if (this.processNewDimensions) {
                        this.processNewDimensions(entry.target.clientWidth, entry.target.clientHeight);
                    }
                });
            });
        }

        // Observes the first element with the "observed" class
        let observedElement = this.#rootNode.querySelector(".observed");
        if (observedElement) {
            this.resizeObserver.observe(observedElement);
        }
    }



    checkLoading(){


        const allDescendants = this.#rootNode.querySelectorAll('*');

        this.#baseComponentChildren = Array.from(allDescendants).filter(child => child instanceof ComponentBase);



        this.#totalChildCount = this.#baseComponentChildren.length;

        if (this.#totalChildCount === 0) {

            this.#finishLoading();

        }else{
            this.#baseComponentChildren.forEach(child => {


                if (child.loaded || child.failed) {


                    this.#loadedChildCount++;

                    this.checkIfAllChildrenLoaded();

                }else{


                    const onChildSettled = event => {

                        // Only the child's own event counts (not ones bubbling up from its descendants),
                        // and it does not propagate further
                        if (event.composedPath()[0] !== child) {
                            return;
                        }
                        event.stopPropagation();

                        this.#loadedChildCount++;

                        this.checkIfAllChildrenLoaded();
                    };

                    child.addEventListener(ComponentBase.LOADED_EVENT, onChildSettled);
                    child.addEventListener(ComponentBase.ERROR_EVENT, onChildSettled);
                }
            });
        }
    }

    checkIfAllChildrenLoaded(){
        // If all children are loaded
        if (this.#loadedChildCount === this.#totalChildCount) {
            this.#finishLoading();
        }
    }

    //An exception in a subclass's onLoad() is reported, but must not stop LOADED_EVENT: otherwise
    //every ancestor (and every whenLoaded() caller) would wait forever.
    #finishLoading() {
        this.#loaded = true;
        try {
            this.onLoad();
        } catch (error) {
            console.error(`[${this.constructor.name}] error in onLoad():`, error);
        }
        this.dispatchEvent(new CustomEvent(ComponentBase.LOADED_EVENT, { bubbles: true, composed: true }));
    }

    /**
     * Subclasses that override connectedCallback/disconnectedCallback must call super: the
     * ResizeObserver is released when the element leaves the document and re-attached if it comes back.
     */
    connectedCallback() {
        if (this.#loaded) {
            this.observe();
        }
    }

    disconnectedCallback() {
        this.resizeObserver?.disconnect();
    }

    adoptedCallback() {}

    loadCSS(address, childUrl) {
        const cssUrl = (childUrl ? ComponentBase.resolveAddress(address, childUrl) : this.resolveAddress(address));

        return ComponentBase.#fetchText(cssUrl).then(text => {
            const style = document.createElement('style');
            style.textContent = text;
            this.#rootNode.appendChild(style);
            return true;
        }).catch(error => {
            console.error(`Could not download CSS ${cssUrl}: ${error.message}`);
            throw error;
        });
    }

    loadScript(scriptAttributes, childUrl) {
        const src = (childUrl ? ComponentBase.resolveAddress(scriptAttributes.src, childUrl) : this.resolveAddress(scriptAttributes.src));
        const integrity = scriptAttributes.integrity || scriptAttributes.integrityHash;
        const key = `${scriptAttributes.type || ''}|${src}`;

        if (!ComponentBase.#scriptCache.has(key)) {
            const promise = new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.async = true;
                script.src = src;

                if (scriptAttributes.type) {
                    script.type = scriptAttributes.type;
                }

                if (integrity) {
                    script.integrity = integrity;
                    script.crossOrigin = "anonymous";
                }

                script.addEventListener("load", () => resolve(true));
                script.addEventListener("error", () => {
                    console.error(`Error loading script: ${script.src}`);
                    reject(new Error(`could not load ${script.src}`));
                });

                //Scripts run in the global scope wherever they're attached, so they go to the
                //document: one that lived inside a component would die with it.
                document.head.appendChild(script);
            });
            ComponentBase.#scriptCache.set(key, promise);
            promise.catch(() => ComponentBase.#scriptCache.delete(key));
        }

        return ComponentBase.#scriptCache.get(key);
    }
}
