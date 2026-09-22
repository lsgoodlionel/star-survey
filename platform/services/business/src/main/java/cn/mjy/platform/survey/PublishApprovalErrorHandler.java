package cn.mjy.platform.survey;

import cn.mjy.platform.survey.SurveyErrorHandler.SurveyError;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 审批流程自有异常的错误映射（与 {@link SurveyErrorHandler} 同一错误体）。单独成类，避免与并行车道改同一文件；
 * 其余异常（404、403 判定原因、409、400）沿用 {@link SurveyErrorHandler}。
 */
@RestControllerAdvice(basePackages = "cn.mjy.platform.survey")
public class PublishApprovalErrorHandler {

    @ExceptionHandler(PublishApprovalForbiddenException.class)
    ResponseEntity<SurveyError> forbidden(PublishApprovalForbiddenException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN).body(new SurveyError(e.code(), e.getMessage(), List.of()));
    }
}
