FROM alpine:3.19

RUN apk add --no-cache nginx jq bash

COPY run.sh /run.sh
RUN sed -i 's/\r$//' /run.sh && chmod a+x /run.sh

CMD [ "/run.sh" ]
