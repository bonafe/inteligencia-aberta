
//Controllers receive events. To do that, they must inherit from EventTarget
export class ControllerBase extends EventTarget{
    constructor(){
        super();
    }
}